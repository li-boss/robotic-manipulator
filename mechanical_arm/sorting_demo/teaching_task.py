"""引导示教业务；所有真实运动仍经Control的当次确认与Controller。"""
import base64
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import time
import uuid
import cv2
import numpy as np
from core import arm
from teaching_plan import COMMON,ROUTES,defaults,digest,pose,prerequisites,fit,locate,readiness,build,numbers,reported_mode,mapping_samples
from teaching_vision import top,matches,background
from ros_client import RosClient

ROOT=Path(__file__).resolve().parent


class Teaching:
    def __init__(self,demo,planner=None):
        self.demo,self.control=demo,demo.control
        self.path=demo.config_path.parent/'teaching_setup.json'
        self.planner=planner or RosClient().request
        self.drafts={};self.control.teaching=self
        self.control.extra_audit_paths.extend(ROOT/name for name in ['teaching_plan.py','teaching_vision.py','teaching_task.py','teaching_ui.js'])
        if self.path.exists():self.control.extra_audit_paths.append(self.path)

    def load(self):return json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else defaults()
    def sha(self):return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    def status(self):
        with self.control.lock:
            cfg=self.load()
            ready=readiness(cfg,self.control.config())
            if reported_mode(cfg):
                try:self.bound(cfg)
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    ready['ready']=False;ready['issues'].append(str(exc))
                    for cell in ready['cells']:cell['enabled']=False
            drafts=[dict(id=d['id'],case=d['case'],pixel=d['plan']['pixel'],cell_id=d['plan']['cell_id'],
                empty=d['empty'],loaded=d['loaded'],saved=d.get('saved',False),plan=d['plan']) for d in self.drafts.values()]
            return dict(setup=cfg,sha256=self.sha(),readiness=ready,
                routes=ROUTES,common_names=COMMON,real=self.control.real,drafts=drafts,
                message=('九点由用户报告验证；新位置为插值试抓，须确认本次完整计划。' if reported_mode(cfg) else
                         '经验映射；不声称精确几何碰撞检查。首轮9个样本，每单元独立验证且整轮至少5个位置。'))

    def save(self,cfg,expected,operation):
        if self.sha()!=expected:raise ValueError('示教配置已被其他操作修改，请刷新状态')
        # 审计内容含实际配置参数；写文件不获得运动授权。
        self.control.audit(dict(operation=operation,setup=cfg,expected_sha256=expected,moves=0))
        encoded=json.dumps(cfg,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.sha()!=expected:raise ValueError('并发修改，未保存')
        if self.path.exists():
            self.path.with_name('teaching_setup.before_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json').write_bytes(self.path.read_bytes())
        temp=self.path.with_suffix('.tmp');temp.write_text(encoded,encoding='utf-8');temp.replace(self.path)
        if self.path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(self.path)
        self.control.pending=None

    def evidence(self,identifier):
        meta=self.demo.auto.snapshot_metadata(identifier)
        if meta.get('real') is not True:raise ValueError('示教需要真实采样证据，禁止把模拟读数当作实测')
        suffixes=['.json'] if meta.get('record_type')=='joint_receipt' else ['.json','.jpg']
        for suffix in suffixes:
            path=self.demo.auto.samples/(identifier+suffix)
            if path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(path)
        return meta

    def capture_joint_receipt(self,fields):
        """抓取阶段只记录明确目标与停稳读数，不访问摄像头、不写舵机。"""
        with self.control.lock:
            self.control.idle();self.control.pending=None
            if fields.get('expected_sha256')!=self.sha():raise ValueError('示教配置已变化')
            if fields.get('kind')!='grasp':raise ValueError('无照片记录仅适用于抓取阶段')
            if not self.control.real:raise ValueError('模拟读数不能保存为实机记录')
            cfg=self.load();arms=self.control.config();targets=pose(arms,fields.get('angles'))
            gripper=arm.angle(arms,6,fields.get('gripper_target'))
            if not cfg['baseline']:raise ValueError('先保存观察基准')
            audit=self.control.audit(dict(operation='record_grasp_without_photo',targets=targets,gripper_target=gripper,moves=0))
            with self.control.hardware():
                rows=self.control.read_rows(arms);second=self.control.read_rows(arms)
            if rows!=second:raise ValueError('关节读数变化，请停稳后重新记录')
            if any(abs(row['angle']-target)>1 for row,target in zip(rows,targets+[gripper])):
                raise ValueError('到位读数与明确目标偏差超过1°，未保存抓取记录')
            identifier=uuid.uuid4().hex
            metadata=dict(id=identifier,record_type='joint_receipt',has_image=False,real=True,
                teaching_kind='grasp',targets=targets,gripper_target=gripper,joints=rows,
                captured_at=int(time.time()*1000),image_size=cfg['baseline']['image_size'],arm_config_sha256=digest(arms))
            self.demo.auto.samples.mkdir(parents=True,exist_ok=True)
            (self.demo.auto.samples/(identifier+'.json')).write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
            return dict(**metadata,audit=audit,status=self.status())

    @staticmethod
    def continue_read_failures(parameters):
        return (parameters.get('operation')=='teaching' and parameters.get('plan',{}).get('task_mode')=='pickup_only'
                and parameters.get('read_failure_policy')=='warn_continue' and parameters.get('observation_confirmed') is True)

    @staticmethod
    def read_warnings(rows,phase):
        return [dict(phase=phase,joint=row['id'],error=row.get('error'),read_ok=False)
                for row in rows if row['read_ok'] is False]

    def observed(self,cfg,rows,allow_failed=False):
        reference=cfg['baseline']
        if not reference:raise ValueError('先保存观察基准')
        for row,raw,target in zip(rows[:5],reference['readings'],reference['angles']):
            if allow_failed and row['read_ok'] is False:continue
            if abs(row['angle']-raw)>1 or abs(row['angle']-target)>1:raise ValueError('不在观察姿态，请手动逐次确认恢复；不自动纠正')
        if cfg.get('task_mode')=='pickup_only' and not (allow_failed and rows[5]['read_ok'] is False) and abs(rows[5]['angle']-self.control.config()['gripper']['open'])>1:
            raise ValueError('抓取任务起始夹爪应张开，请恢复观察姿态后重新预览')

    def bound(self,cfg):
        if cfg['arm_config_sha256']!=digest(self.control.config()):raise ValueError('机械臂配置已变化，请重新准备示教')
        for record in [cfg['baseline']]+list(cfg['common'].values()):
            if record:self.evidence(record['frame_id'])
        for record in cfg['samples']+cfg['validations']:
            proof=record.get('evidence',{});filename=proof.get('file')
            if not isinstance(filename,str) or filename!=record['id']+'.json' or not all(c in '0123456789abcdef' for c in record['id']):
                raise ValueError('验证证据编号无效')
            path=self.path.parent/'logs/teaching'/filename
            if hashlib.sha256(path.read_bytes()).hexdigest()!=proof.get('sha256'):raise ValueError('真实验证证据缺失或已改变')
            if path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(path)
        if reported_mode(cfg):
            source=self.demo.cards.reported_source(cfg)
            saved={key:value for key,value in cfg.get('reported_mapping',{}).items() if key!='fit'}
            if source!=saved:raise ValueError('九点照片、中心、目标或顺序已改变，请重新建立映射')

    def capture(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();expected=fields.get('expected_sha256')
            if expected!=self.sha():raise ValueError('示教配置版本已改变')
            kind=fields.get('kind');arms=self.control.config()
            if kind not in ['reference','target','above','grasp']+COMMON:raise ValueError('未知采样阶段')
            targets=None if kind=='target' else pose(arms,fields.get('angles'))
            gripper_target=arm.angle(arms,6,fields['gripper_target']) if 'gripper_target' in fields else None
            if kind=='target':self.bound(cfg)
            self.control.audit(dict(operation='teaching_capture',kind=kind,angles=targets,moves=0))
            result=self.demo.auto.capture()
            rows=result['joints']
            if gripper_target is not None and abs(rows[5]['angle']-gripper_target)>1:raise ValueError('夹爪回读与明确目标偏差超过1°，未保存姿态')
            if targets and any(abs(r['angle']-v)>1 for r,v in zip(rows[:5],targets)):raise ValueError('原始读数与明确目标偏差超过1°，未保存姿态')
            if kind=='target':self.observed(cfg,rows)
            image=cv2.imdecode(np.frombuffer(base64.b64decode(result['jpeg_base64']),np.uint8),cv2.IMREAD_COLOR)
            metadata=self.demo.auto.snapshot_metadata(result['id'])
            metadata.update(teaching_kind=kind,targets=targets,arm_config_sha256=digest(arms))
            if kind=='reference':
                low,high=self.demo.config['colors']['yellow']
                mask=cv2.inRange(cv2.cvtColor(image,cv2.COLOR_BGR2HSV),np.asarray(low),np.asarray(high))
                if any(cv2.contourArea(c)>=self.demo.config['min_area'] for c in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[-2]):
                    raise ValueError('观察基准必须为空桌面，请先移走黄色木块')
            if kind=='target':
                if result['image_size']!=cfg['baseline']['image_size']:raise ValueError('分辨率变化，请重新准备')
                metadata['target']=top(image,self.demo.config)
                metadata['baseline_id']=cfg['baseline']['frame_id']
                metadata['scene']=self.scene(cfg,image)
            (self.demo.auto.samples/(result['id']+'.json')).write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
            if kind=='reference' or kind in COMMON:
                record=dict(frame_id=result['id'],angles=targets,readings=[r['angle'] for r in rows[:5]],image_size=result['image_size'])
                if kind=='reference':
                    cfg=defaults();cfg['baseline']=record
                else:
                    if not cfg['baseline']:raise ValueError('先记录空桌面观察姿态')
                    cfg['common'][kind]=record;cfg['samples']=[];cfg['fit']=None;cfg['validations']=[];cfg['reference_verified']=False
                cfg['arm_config_sha256']=digest(arms)
                self.save(cfg,expected,'record_teaching_'+kind);self.drafts.clear()
            return dict(**result,target=metadata.get('target'),scene=metadata.get('scene'),status=self.status())

    def scene(self,cfg,image):
        reference=cv2.imread(str(self.demo.auto.samples/(cfg['baseline']['frame_id']+'.jpg')))
        if reference is None or reference.shape!=image.shape:raise ValueError('观察参考画面缺失或分辨率改变')
        return background(reference,image,self.demo.config)

    def target(self,cfg,confirm):
        image,jpeg,sequence,captured=self.demo.auto.frame()
        if [image.shape[1],image.shape[0]]!=cfg['baseline']['image_size']:raise ValueError('分辨率变化，请重新准备')
        found=top(image,self.demo.config);scene=self.scene(cfg,image)
        if confirm is not True:raise ValueError('请当次确认相机安装、桌面、木块与线缆未改变')
        return dict(**found,scene=scene,frame_id=sequence,captured_at=captured,
                    jpeg_base64=base64.b64encode(jpeg).decode())

    def orders(self,fields):
        with self.control.lock:
            self.control.idle();cfg=self.load()
            orders=fields.get('orders')
            if not isinstance(orders,dict) or set(orders)!=set(ROUTES):raise ValueError('需要全部阶段的明确关节顺序')
            for value in orders.values():
                if not isinstance(value,list) or len(value)!=5 or set(value)!=set(range(1,6)) or any(type(x)is not int for x in value):
                    raise ValueError('每阶段顺序须为1～5号各一次')
            cfg['orders']=orders;cfg['samples']=[];cfg['fit']=None;cfg['validations']=[];cfg['reference_verified']=False
            self.save(cfg,fields.get('expected_sha256'),'save_teaching_orders');self.drafts.clear()
            return self.status()

    def checked_plan(self,cfg,arms,pixel,pair,require):
        args=dict(operation='teaching',setup=cfg,arms=arms,pixel=pixel,pair=pair,require_validated=require)
        planned=self.planner(args);checked=build(cfg,arms,pixel,pair,require)
        if planned!=checked:raise ValueError('ROS2示教计划与本地重新计算不一致')
        return checked

    def sample_draft(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();arms=self.control.config();prerequisites(cfg,arms);self.bound(cfg)
            if fields.get('expected_sha256')!=self.sha():raise ValueError('配置已变化')
            if fields.get('center_confirmed') is not True:raise ValueError('请明确核对冻结画面的顶面中心')
            source=self.evidence(fields.get('target_frame'))
            if source.get('teaching_kind')!='target' or source.get('baseline_id')!=cfg['baseline']['frame_id'] or source['arm_config_sha256']!=digest(arms):
                raise ValueError('必须先在本次观察姿态冻结木块，再记录取物姿态')
            point=numbers(fields.get('pixel'),2,'顶面中心')
            if point.shape!=(2,) or not np.isfinite(point).all() or np.linalg.norm(point-source['target']['pixel'])>2:
                raise ValueError('请在冻结画面明确核对检测到的顶面中心，偏差超过2像素则重新调整观察')
            pair={};receipts={}
            for kind in ['above','grasp']:
                record=self.evidence(fields.get(kind+'_frame'))
                if record.get('teaching_kind')!=kind or record['arm_config_sha256']!=digest(arms) or record['captured_at']<=source['captured_at']:
                    raise ValueError('姿态须在木块定位画面之后采样，且与当前配置匹配')
                pair[kind]=pose(arms,record['targets']);receipts[kind]=record['id']
            if cfg.get('task_mode')=='pickup_only':
                pair['stage_orders']=fields.get('stage_orders')
                pair['stage_motion_ms']=fields.get('stage_motion_ms') or {}
            self.control.audit(dict(operation='teaching_sample_draft',fields=fields,moves=0))
            plan=self.checked_plan(cfg,arms,point.tolist(),pair,False)
            return self.new_draft(cfg,plan,'sample',dict(pixel=point.tolist(),descriptor=source['target']['descriptor'],
                target_frame=source['id'],above=pair['above'],grasp=pair['grasp'],receipts=receipts,
                **({'stage_orders':pair['stage_orders'],'stage_motion_ms':pair['stage_motion_ms']} if cfg.get('task_mode')=='pickup_only' else {})))

    def new_draft(self,cfg,plan,case,sample):
        identifier=uuid.uuid4().hex
        audit=self.control.audit(dict(operation='teaching_validation_plan',case=case,plan=plan,sample=sample,moves=0))
        item=dict(id=identifier,case=case,plan=plan,sample=sample,sha256=self.sha(),hashes=audit['hashes'],
            empty=dict(next=0,status='pending',evidence=[]),loaded=dict(next=0,status='pending',evidence=[]),saved=False)
        self.drafts[identifier]=item
        return dict(draft=item,audit=audit,message='没有运动。先移走木块逐步验证空载路径，再放回同一位置逐步验证带块；每步单独预览确认。')

    def preview(self,fields):
        if fields.get('action')=='step':return self.step_preview(fields)
        if fields.get('action')=='sample':return self.sample_draft(fields)
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();arms=self.control.config();prerequisites(cfg,arms);self.bound(cfg)
            if self.control.real and arms.get('hardware_ready') is not True:raise ValueError('实机未就绪')
            tolerate=fields.get('action')=='auto' and cfg.get('task_mode')=='pickup_only'
            if tolerate and fields.get('scene_confirmed') is not True:
                raise ValueError('请当次确认处于固定观察姿态、夹爪张开及现场条件')
            audit=self.control.audit(dict(operation='teaching_preview',fields=fields))
            with self.control.hardware():
                rows=self.control.read_rows(arms,allow_failed=tolerate);self.observed(cfg,rows,allow_failed=tolerate);target=self.target(cfg,fields.get('scene_confirmed'))
            cell,_=locate(cfg,target['pixel']);matches(target,[mapping_samples(cfg)[i] for i in cell['indices']])
            validation=fields.get('action')=='validation'
            if fields.get('action') not in ['validation','auto']:raise ValueError('未知预览操作')
            if validation:
                if reported_mode(cfg):raise ValueError('独立程序验证请先切换到真实采集模式；九点用户报告模式直接预览试抓')
                if any(np.linalg.norm(np.asarray(s['pixel'])-target['pixel'])<max(5,target['descriptor']['width']/2) for s in cfg['samples']+cfg['validations']):
                    raise ValueError('独立验证位置须与样本和既有验证位置分开')
            plan=self.checked_plan(cfg,arms,target['pixel'],None,not validation)
            if self.control.audit(dict(operation='teaching_recheck'))['hashes']!=audit['hashes']:raise ValueError('规划期间规则或配置改变')
            if validation:
                result=self.new_draft(cfg,plan,'validation',dict(pixel=target['pixel'],descriptor=target['descriptor'],cell_id=cell['id']))
                result['target']=target
                return result
            token=secrets.token_urlsafe(24);args=dict(operation='teaching',plan=plan,scene_confirmed=True,moves=len(plan['steps']))
            if tolerate:
                args.update(read_failure_policy='warn_continue',observation_confirmed=True,
                            target_policy='confirmed_preview',preview_target={key:target[key] for key in ['pixel','frame_id','captured_at']})
            final=self.control.audit(args)
            self.control.pending=dict(token=token,expires=time.monotonic()+120,audit=final,parameters=args,
                rows=rows,target=target,config_bytes=self.control.path.read_bytes())
            return dict(token=token,expires_in=120,parameters=args,joints=rows,target=target,audit=final,
                read_warnings=self.read_warnings(rows,'preview'),
                mode='real' if self.control.real else 'simulation',conditions='经验映射未经精确几何避障；核对完整路径、相机安装、桌面、线缆及停止方式。仅本次有限动作，不重试、复位或松爪。'+
                ('\n执行使用本次预览中人工确认的中心与完整计划，不再次拍照或识别；确认前确保方块位置未移动。\n读数失败仅记录警告并继续；起始姿态缺失读数依照本次人工确认。有效读数偏差、下发异常或参数非法仍停止。' if tolerate else ''))

    def draft(self,identifier):
        item=self.drafts.get(identifier)
        if not item or item['saved']:raise ValueError('验证计划不存在、已保存或服务已重启')
        audit=self.control.audit(dict(operation='check_teaching_draft',id=identifier))
        if item['sha256']!=self.sha() or item['hashes']!=audit['hashes']:raise ValueError('代码、规则或示教配置变化，请重新生成验证计划')
        return item

    def step_preview(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            item=self.draft(fields.get('draft_id'));mode=fields.get('mode')
            if mode not in ['empty','loaded']:raise ValueError('请选择空载或带块验证')
            if mode=='loaded' and item['empty']['status']!='completed':raise ValueError('必须先完整验证空载路径')
            progress=item[mode];index=fields.get('step')
            if progress['status'] in ['failed','completed'] or type(index)is not int or index!=progress['next']:raise ValueError('仅能按顺序预览下一步；失败后须新建计划')
            if fields.get('scene_confirmed') is not True or (mode=='empty' and fields.get('empty_confirmed') is not True):
                raise ValueError('请当次核对现场，空载验证须确认已移走木块')
            step=item['plan']['steps'][index];joint,value=step['joints'][0]
            preview=self.control.preview(dict(operation='move',joint=joint,angle=value,motion_ms=step['motion_ms']))
            try:
                self.step_start(item,mode,index,preview['joints'])
                if index==0 and mode=='loaded':self.recheck_target(self.load(),item['sample'],True)
            except Exception:
                self.control.pending=None
                raise
            args=dict(preview['parameters'],teaching_step=dict(draft_id=item['id'],mode=mode,step=index),
                scene_confirmed=True,empty_confirmed=mode=='empty')
            checked=self.control.audit(args)
            self.control.pending['parameters']=args;self.control.pending['audit']=checked
            preview.update(parameters=args,audit=checked)
            return preview

    def step_start(self,item,mode,index,rows):
        cfg=self.load();expected={i+1:v for i,v in enumerate(cfg['baseline']['angles'])}
        if cfg.get('task_mode')=='pickup_only':expected[6]=self.control.config()['gripper']['open']
        for step in item['plan']['steps'][:index]:
            for joint,value in step['joints']:expected[joint]=value
        for joint,value in expected.items():
            if abs(rows[joint-1]['angle']-value)>1:raise ValueError('起始姿态与验证路径不一致，请恢复后重新预览')

    def validate_step(self,p,rows):
        ctx=p['parameters']['teaching_step'];item=self.draft(ctx['draft_id'])
        if item[ctx['mode']]['next']!=ctx['step'] or item[ctx['mode']]['status'] in ['failed','completed']:raise ValueError('验证步骤已执行或失效')
        self.step_start(item,ctx['mode'],ctx['step'],rows)
        if ctx['step']==0 and ctx['mode']=='loaded':self.recheck_target(self.load(),item['sample'],True)

    def step_finished(self,p,rows,error=None):
        ctx=p['parameters']['teaching_step'];item=self.drafts.get(ctx['draft_id'])
        if not item:return
        progress=item[ctx['mode']]
        if error:
            progress['status']='failed';progress['error']=str(error)
        else:
            progress['evidence'].append(dict(token=p['token'],step=ctx['step'],joints=rows,real=self.control.real))
            progress['next']+=1
            progress['status']='completed' if progress['next']==len(item['plan']['steps']) else 'running'
        self.control.record('teaching_step_result',dict(draft_id=item['id'],mode=ctx['mode'],progress=progress))

    def recheck_target(self,cfg,reference,confirmed):
        found=self.target(cfg,confirmed)
        if np.linalg.norm(np.asarray(found['pixel'])-reference['pixel'])>min(2,found['descriptor']['width']*.05):raise ValueError('木块位置变化，请重新定位并生成计划')
        matches(found,[reference]);return found

    def persist_verified(self,fields,case):
        with self.control.lock:
            self.control.idle();item=self.draft(fields.get('draft_id'))
            if item['case']!=case or fields.get('success') is not True:raise ValueError('请明确确认本次计划执行成功')
            if not self.control.real or any(item[m]['status']!='completed' or len(item[m]['evidence'])!=len(item['plan']['steps']) or not all(e['real'] for e in item[m]['evidence']) for m in ['empty','loaded']):
                raise ValueError('需全部真实空载和带块逐步执行证据，模拟或仅勾选不能通过')
            cfg=self.load()
            proof=self.path.parent/'logs/teaching'
            proof.mkdir(parents=True,exist_ok=True)
            proof_file=proof/(item['id']+'.json')
            proof_file.write_text(json.dumps(dict(id=item['id'],case=case,plan=item['plan'],sample=item['sample'],
                empty=item['empty'],loaded=item['loaded'],operator_success=True),ensure_ascii=False),encoding='utf-8')
            evidence=dict(file=item['id']+'.json',sha256=hashlib.sha256(proof_file.read_bytes()).hexdigest())
            if case=='sample':
                sample=dict(item['sample'],id=item['id'],verified=True,evidence=evidence)
                if any(np.linalg.norm(np.asarray(x['pixel'])-sample['pixel'])<5 for x in cfg['samples']):raise ValueError('重复样本位置')
                cfg['samples'].append(sample);cfg['fit']=None;cfg['validations']=[];cfg['reference_verified']=True
            else:
                cfg['validations'].append(dict(id=item['id'],pixel=item['sample']['pixel'],cell_id=item['sample']['cell_id'],
                    fit_version=cfg['fit']['version'],passed=True,evidence=evidence))
            self.save(cfg,fields.get('expected_sha256'),'save_verified_teaching_'+case);item['saved']=True
            return self.status()

    def samples(self,fields):
        action=fields.get('action')
        if action=='orders':return self.orders(fields)
        if action=='save':return self.persist_verified(fields,'sample')
        if action=='result':return self.demo.auto.result(fields)
        if action=='delete':
            with self.control.lock:
                self.control.idle();cfg=self.load();identifier=fields.get('sample_id')
                if not any(x['id']==identifier for x in cfg['samples']):raise ValueError('样本不存在')
                cfg['samples']=[x for x in cfg['samples'] if x['id']!=identifier];cfg['fit']=None;cfg['validations']=[]
                self.save(cfg,fields.get('expected_sha256'),'delete_teaching_sample');self.drafts.clear();return self.status()
        raise ValueError('未知样本操作')

    def fit(self,fields):
        with self.control.lock:
            self.control.idle();cfg=self.load();cfg.pop('mapping_mode',None);self.bound(cfg)
            for sample in cfg['samples']:
                self.evidence(sample['target_frame'])
                for identifier in sample['receipts'].values():self.evidence(identifier)
            cfg['fit']=fit(cfg,self.control.config());cfg['validations']=[]
            self.save(cfg,fields.get('expected_sha256'),'fit_teaching_mapping');self.drafts.clear();return self.status()

    def validation(self,fields):
        if fields.get('action')=='failed':
            with self.control.lock:
                self.control.idle();item=self.draft(fields.get('draft_id'))
                if item['case']!='validation':raise ValueError('不是独立验证计划')
                cfg=self.load();cell=item['sample']['cell_id']
                cfg['validations']=[v for v in cfg['validations'] if v['cell_id']!=cell]
                self.save(cfg,fields.get('expected_sha256'),'failed_teaching_cell');item['saved']=True;return self.status()
        return self.persist_verified(fields,'validation')

    def validate_execution(self,p):
        cfg=self.load();self.bound(cfg)
        parameters=p['parameters'];plan=parameters['plan']
        checked=build(cfg,self.control.config(),plan['pixel'])
        if parameters.get('target_policy')=='confirmed_preview':
            if cfg.get('task_mode')!='pickup_only' or plan.get('task_mode')!='pickup_only' or parameters.get('scene_confirmed') is not True:
                raise ValueError('人工确认预览只适用于本次明确的夹持任务')
            reference={key:p['target'][key] for key in ['pixel','frame_id','captured_at']}
            if parameters.get('preview_target')!=reference or reference['pixel']!=plan['pixel'] or plan!=checked:
                raise ValueError('已确认的预览中心或完整计划被改变，请重新预览')
            return  # 已确认冻结预览；禁止在接受确认及运动线程中重新读取相机/识别。
        self.recheck_target(cfg,p['target'],p['parameters'].get('scene_confirmed'))

    def run(self,controller,p):
        self.validate_execution(p);plan=p['parameters']['plan']
        for step in plan['steps']:
            if len(step['joints'])!=1:raise ValueError('每步只能一个关节')
            for joint,value in step['joints']:arm.angle(controller.config,joint,value)
            arm.timing(dict(controller.config,motion_ms=step['motion_ms'],settle_ms=step['settle_ms']))
        tolerate=self.continue_read_failures(p['parameters'])
        task=dict(task_id=p['token'],mode='real' if controller.real else 'simulation',color='yellow',strategy='teaching',
                  started_at=arm.now(),status='running',result='unconfirmed',retry_count=0,plan=plan,
                  target_policy=p['parameters'].get('target_policy','recheck_live'),target_reference=p['parameters'].get('preview_target'),
                  read_warnings=list(p.get('read_warnings',[])))
        self.demo.store.append(task)
        try:
            # 缺失读数不补造；expected 是人工确认的起始目标及已下发目标，不是实测读数。
            if tolerate:
                expected=list(self.load()['baseline']['angles'])+[controller.config['gripper']['open']]
                for i,row in enumerate(p['rows']):
                    if row['read_ok']:expected[i]=row['angle']
            else:expected=[r['angle'] for r in p['rows']]
            for index,step in enumerate(plan['steps']):
                if self.control.audit(p['parameters'])['hashes']!=p['audit']['hashes']:raise ValueError('执行期间代码或配置改变')
                self.control.record('teaching_sending',dict(id=p['token'],step=index,command=step))
                controller.config=dict(controller.config,motion_ms=step['motion_ms'],settle_ms=step['settle_ms'])
                controller.write([tuple(pair) for pair in step['joints']])
                for joint,value in step['joints']:expected[joint-1]=value
                with self.control.lock:
                    for joint,value in step['joints']:self.control.commanded[str(joint)]=value
                rows=self.control.read_rows(controller.config,allow_failed=tolerate)
                if tolerate:task['read_warnings'].extend(self.read_warnings(rows,'step_'+str(index+1)))
                if any(r['read_ok'] and abs(r['angle']-v)>1 for r,v in zip(rows,expected)):raise ValueError('回读或其他关节偏差超过1°，停止后续指令')
            task.update(status='completed',ended_at=arm.now());self.demo.store.append(task)
            message='夹持步骤完成，保持抓取姿态；结果待人工确认' if plan.get('task_mode')=='pickup_only' else '有限抓放已完成，结果待人工确认'
            if task['read_warnings']:message+='；'+str(len(task['read_warnings']))+'条读取失败警告，已按确认计划继续，缺失读数未确认到位'
            return dict(status='completed',task_id=p['token'],read_warnings=task['read_warnings'],message=message)
        except Exception as exc:
            task.update(status='failed',result='failed',failure_reason=str(exc),ended_at=arm.now());self.demo.store.append(task);raise
