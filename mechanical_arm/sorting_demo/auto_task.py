"""自动任务网页业务；复用Control的授权、审计和硬件锁。"""
import base64
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
import uuid

import cv2
import numpy as np
from core import arm, detect
from auto_geometry import array, calibration_report, find_top, pixel_to_base
from auto_plan import default_setup, model_report, parameters, readiness, build_plan, arm_fingerprint
from ros_client import RosClient

ROOT=Path(__file__).resolve().parent


class AutoTask:
    def __init__(self,demo,planner=None):
        self.demo,self.control=demo,demo.control
        self.path=demo.config_path.parent/'auto_setup.json'
        self.samples=self.path.parent/'logs/calibration'
        self.planner=planner or RosClient()
        self.control.auto_task=self
        self.control.extra_audit_paths=[ROOT/n for n in ['auto_task.py','auto_geometry.py','auto_plan.py',
             'ros_client.py','ros_planner.py','auto_ui.js','models/course/dofbot.urdf','models/course/provenance.json']]
        self.control.extra_audit_paths.append(self.demo.config_path)
        # 文件部署时创建默认配置；测试或旧安装可只读展示缺失配置。
        if self.path.exists():self.control.extra_audit_paths.append(self.path)

    def load(self):
        return json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else default_setup()

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    def status(self):
        with self.control.lock:
            cfg=self.load()
            return dict(setup=cfg,setup_sha256=self.digest(),readiness=readiness(cfg,self.control.config()),
                        real=self.control.real,job=self.control.job,
                        message='首次测量与空载路径验证完成后，才可批准完整自动抓放')

    def snapshot_metadata(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):
            raise ValueError('无效标定画面编号')
        return json.loads((self.samples/(identifier+'.json')).read_text(encoding='utf-8'))

    def save(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            if fields.get('expected_sha256')!=self.digest():raise ValueError('配置已变化，请刷新后重填')
            cfg=fields.get('setup')
            if not isinstance(cfg,dict) or set(cfg)!=set(default_setup()):raise ValueError('请保留完整的向导配置结构')
            old=self.load();flags=['model_reviewed','calibration_reviewed','path_reviewed']
            without_flags=lambda x:{k:v for k,v in x.items() if k not in flags+['arm_config_sha256']}
            changed=without_flags(cfg)!=without_flags(old)
            cfg=copy.deepcopy(cfg)
            if cfg['version']!=1:raise ValueError('不支持的标定配置版本')
            if changed:
                for flag in flags:cfg[flag]=False
                cfg['arm_config_sha256']=None
            elif any(cfg.get(flag) is True for flag in flags):
                cfg['arm_config_sha256']=arm_fingerprint(self.control.config())
            else:cfg['arm_config_sha256']=None
            if any(type(cfg.get(flag))is not bool for flag in flags):raise ValueError('核对状态必须为布尔值')
            if cfg['calibration_reviewed'] is True:
                calibration_report(cfg)
                self.check_capture_evidence(cfg)
            if cfg['model_reviewed'] is True:model_report(cfg,self.control.config())
            if cfg['path_reviewed'] is True:
                if cfg['calibration_reviewed'] is not True or cfg['model_reviewed'] is not True:
                    raise ValueError('先核对模型与标定，再登记现场路径验证')
                parameters(cfg,self.control.config())
            self.control.audit(dict(operation='save_auto_setup',expected_sha256=fields.get('expected_sha256'),
                                    setup=cfg,reset_reviews=changed))
            original=self.path.read_bytes() if self.path.exists() else None
            if self.digest()!=fields.get('expected_sha256'):raise ValueError('配置并发修改，未保存')
            if original is not None:
                backup=self.path.with_name('auto_setup.before_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
                backup.write_bytes(original)
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            temp.replace(self.path)
            if self.path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(self.path)
            return dict(status=self.status(),message='已保存；测量数据改变后自动清除核对与路径验证状态')

    def frame(self):
        camera=self.demo.camera
        with camera.condition:
            state=camera.status()
            if not state['connected']:raise ValueError('摄像头未连接')
            jpeg=bytes(camera.jpeg);sequence=camera.sequence
            captured=int((time.time()-(time.monotonic()-camera.updated))*1000)
        if int(time.time()*1000)-captured>1000:raise ValueError('摄像头画面已过期')
        frame=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
        if frame is None:raise ValueError('图像解码失败')
        return frame,jpeg,sequence,captured

    def capture(self):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            if not self.control.real:raise ValueError('标定采样需要实机原始读数，模拟模式不能采样')
            audit=self.control.audit(dict(operation='capture_calibration_frame',moves=0))
            with self.control.hardware():
                rows=self.control.read_rows(self.control.config())
                frame,jpeg,sequence,captured=self.frame()
                second=self.control.read_rows(self.control.config())
                if rows!=second:raise ValueError('关节读数变化，请停稳后重新采样')
            identifier=uuid.uuid4().hex
            self.samples.mkdir(parents=True,exist_ok=True)
            metadata=dict(id=identifier,joints=rows,image_size=[frame.shape[1],frame.shape[0]],
                          captured_at=captured,frame_id=sequence,real=True)
            (self.samples/(identifier+'.jpg')).write_bytes(jpeg)
            (self.samples/(identifier+'.json')).write_text(json.dumps(metadata),encoding='utf-8')
            return dict(**metadata,jpeg_base64=base64.b64encode(jpeg).decode(),audit=audit)

    def intrinsics(self,fields):
        with self.control.lock:
            self.control.idle()
            xy=array(fields.get('xy_mm'),(4,2),'固定标记坐标')
            inputs=fields.get('samples')
            if not isinstance(inputs,list) or not 10<=len(inputs)<=30:
                raise ValueError('内参标定需要10～30幅不同观察姿态的画面')
            points=[];poses=[];size=None
            for entry in inputs:
                meta=self.snapshot_metadata(entry['id'])
                if size and meta['image_size']!=size:raise ValueError('样本分辨率不一致')
                size=meta['image_size'];poses.append(tuple(r['angle'] for r in meta['joints'][:5]))
                points.append(array(entry['pixels'],(4,2),'角点像素').astype(np.float32))
            if len(set(poses))<5:raise ValueError('样本须包含至少五种不同姿态，不要只平移画面点选')
            obj=np.column_stack([xy-xy[0],np.zeros(4)]).astype(np.float32)
            guess=np.array([[size[0],0,size[0]/2],[0,size[0],size[1]/2],[0,0,1]],float)
            rms,k,d,rotations,translations=cv2.calibrateCamera([obj]*len(points),points,tuple(size),guess,None,
                                                             flags=cv2.CALIB_USE_INTRINSIC_GUESS)
            normals=np.asarray([cv2.Rodrigues(r)[0][:,2] for r in rotations])
            if rms>1 or np.linalg.matrix_rank(normals-normals.mean(axis=0),tol=.01)<2:
                raise ValueError('内参样本误差过大或视角不足，请重新采样')
            self.control.audit(dict(operation='fit_camera_intrinsics',samples=inputs,rms_px=float(rms)))
            return dict(matrix=k.tolist(),distortion=d.reshape(-1).tolist(),image_size=size,samples=inputs,rms_px=float(rms))

    def anchor(self,fields):
        # 用冻结画面和实机读数建立观察姿态，禁止用模拟角度填充。
        cfg=copy.deepcopy(fields['setup']);meta=self.snapshot_metadata(fields['frame_id'])
        cfg['observation_readings']=[r['angle'] for r in meta['joints'][:5]]
        cfg['camera']['anchor_frame']=meta['id']
        return self.save(dict(setup=cfg,expected_sha256=fields.get('expected_sha256')))

    def check_capture_evidence(self,cfg):
        anchor=self.snapshot_metadata(cfg['camera']['anchor_frame'])
        recorded=[r['angle'] for r in anchor['joints'][:5]]
        if recorded!=cfg['observation_readings'] or anchor['image_size']!=cfg['camera']['image_size']:
            raise ValueError('观察姿态或分辨率与冻结画面证据不匹配')
        for entry in [cfg['check_point']]+cfg['height_checks']:
            metadata=self.snapshot_metadata(entry.get('frame_id'))
            if metadata['image_size']!=anchor['image_size'] or np.max(abs(np.asarray([r['angle'] for r in metadata['joints'][:5]])-recorded))>1:
                raise ValueError('检查点必须在同一观察姿态与分辨率下采集')
        return anchor

    def observation(self, cfg, rows):
        reference=array(cfg['observation_readings'],(5,),'标定观察读数')
        if np.max(abs(np.asarray([r['angle'] for r in rows[:5]])-reference))>1:
            raise ValueError('机械臂不在标定观察姿态；请通过逐次确认的手动面板恢复')

    def target(self,cfg):
        frame,jpeg,sequence,captured=self.frame()
        found=detect(frame,self.demo.config,sequence,captured)
        if found['result']!='detected' or found['color']!='yellow':raise ValueError(found.get('reason') or '必须单块黄色木块')
        anchor=cfg['camera'].get('anchor_frame')
        self.snapshot_metadata(anchor)
        reference=cv2.imread(str(self.samples/(anchor+'.jpg')),cv2.IMREAD_GRAYSCALE)
        if reference is None:raise ValueError('标定参考画面缺失')
        points=array(cfg['marks']['pixels'],(4,2),'标记').astype(np.float32).reshape(-1,1,2)
        current=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        tracked,status,error=cv2.calcOpticalFlowPyrLK(reference,current,points,None)
        if tracked is None or not np.all(status):raise ValueError('标记无法跟踪，请保持可见并重新核对观察姿态')
        back,ok,_=cv2.calcOpticalFlowPyrLK(current,reference,tracked,None)
        if back is None or not np.all(ok) or np.max(np.linalg.norm(back-points,axis=2))>.5:
            raise ValueError('标记跟踪不可靠，未生成运动计划')
        for original,new in zip(points.reshape(4,2),tracked.reshape(4,2)):
            delta=np.linalg.norm(pixel_to_base(cfg,new.tolist(),cfg['table_z_mm'])-pixel_to_base(cfg,original.tolist(),cfg['table_z_mm']))
            if delta>cfg['max_position_error_mm']/3:raise ValueError('相机或标记位置变化，须重新标定')
        target=find_top(frame,cfg,self.demo.config)
        return dict(**target,frame_id=sequence,captured_at=captured)

    def preview(self,dry_run=False):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();arms=self.control.config()
            # 在硬件初始化之前核对所有必需配置。
            calibration_report(cfg);model_report(cfg,arms);parameters(cfg,arms)
            self.check_capture_evidence(cfg)
            for entry in [cfg['camera']['anchor_frame'],cfg['check_point']['frame_id']]+[x['frame_id'] for x in cfg['height_checks']]:
                for suffix in ['.json','.jpg']:
                    path=self.samples/(entry+suffix)
                    if path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(path)
            if not dry_run and not readiness(cfg,arms)['ready']:raise ValueError('尚未完成现场路径验证')
            if self.control.real and arms.get('hardware_ready') is not True:raise ValueError('实机未就绪')
            initial_audit=self.control.audit(dict(operation='auto_plan',dry_run=dry_run))
            with self.control.hardware():
                rows=self.control.read_rows(arms);self.observation(cfg,rows)
                target=self.target(cfg)
            plan=self.planner(cfg,arms,target['xyz_mm'],not dry_run)
            # 不信任ROS输出的原始舵机角度；本地重新构建并比较全部路径。
            checked=build_plan(cfg,arms,target['xyz_mm'],not dry_run)
            if plan!=checked:raise ValueError('ROS2计划与本地检查结果不一致')
            args=dict(operation='auto',plan=plan,dry_run=dry_run,moves=len(plan['steps']))
            audit=self.control.audit(args)
            if audit['hashes']!=initial_audit['hashes']:raise ValueError('规划期间配置或代码改变，请重新预览')
            if dry_run:return dict(plan=plan,target=target,audit=audit,dry_run=True,
                                   message='仅供空载逐段验证，不能整批执行')
            token=secrets.token_urlsafe(24)
            self.control.pending=dict(token=token,expires=time.monotonic()+120,audit=audit,parameters=args,
                                      rows=rows,target=target,config_bytes=self.control.path.read_bytes())
            return dict(token=token,expires_in=120,parameters=args,joints=rows,audit=audit,target=target,
                        mode='real' if self.control.real else 'simulation',
                        conditions='本次仅执行以上有限步骤；木块静止、现场路径与线缆已核对；页面不是硬件急停；失败不重试或松爪。')

    def validate_execution(self,p):
        cfg=self.load()
        if not readiness(cfg,self.control.config())['ready']:raise ValueError('自动任务前置条件不满足')
        target=self.target(cfg)
        if np.linalg.norm(np.asarray(target['xyz_mm'])-p['target']['xyz_mm'])>cfg['max_position_error_mm']:
            raise ValueError('木块位置变化，请重新规划并确认')

    def run(self,controller,p):
        plan=p['parameters']['plan'];self.validate_execution(p)
        for step in plan['steps']:
            for joint,value in step['joints']:arm.angle(controller.config,joint,value)
            arm.timing(dict(controller.config,motion_ms=step['motion_ms'],settle_ms=step['settle_ms']))
        task=dict(task_id=p['token'],mode='real' if controller.real else 'simulation',color='yellow',
                  started_at=arm.now(),status='running',result='unconfirmed',retry_count=0,plan=plan)
        self.demo.store.append(task)
        try:
            for index,step in enumerate(plan['steps']):
                audit=self.control.audit(p['parameters'])
                if audit['hashes']!=p['audit']['hashes']:raise ValueError('执行期间代码或配置改变，停止后续指令')
                self.control.record('auto_sending',dict(id=p['token'],step=index,command=step))
                controller.config=dict(controller.config,motion_ms=step['motion_ms'],settle_ms=step['settle_ms'])
                controller.write([tuple(pair) for pair in step['joints']])
                rows=self.control.read_rows(controller.config)
                for joint,value in step['joints']:
                    if abs(rows[joint-1]['angle']-value)>1:raise ValueError('本次目标回读偏差超过1°，停止后续指令')
                    with self.control.lock:self.control.commanded[str(joint)]=value
            task.update(status='completed',ended_at=arm.now())
            self.demo.store.append(task)
            return dict(status='completed',task_id=p['token'],message='有限动作已完成，抓取和放置结果待人工确认')
        except Exception as exc:
            task.update(status='failed',result='failed',failure_reason=str(exc),ended_at=arm.now())
            self.demo.store.append(task);raise

    def result(self,fields):
        with self.control.lock:
            self.control.idle()
            job=self.control.job
            if not job or job.get('task_id')!=fields.get('task_id') or job['status']!='completed':
                raise ValueError('没有已完成、待确认的本次自动任务')
            result=fields.get('result')
            if result not in ('success','failed'):raise ValueError('请明确报告成功或失败')
            events,_=self.demo.store.latest()
            task=next(x for x in events if x['task_id']==fields['task_id'])
            if task['result']!='unconfirmed':return task
            task.update(result=result,failure_reason=fields.get('reason') if result=='failed' else None)
            self.control.audit(dict(operation='confirm_auto_result',task_id=task['task_id'],result=result))
            self.demo.store.append(task);return task
