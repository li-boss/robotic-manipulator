"""独立完整抓放业务；仅显式确认后通过原Controller执行有限计划。"""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid

import cv2
import numpy as np

from core import arm
from auto_task import AutoTask
from auto_plan import arm_fingerprint
import cad_geometry
from manipulation_geometry import (ASSETS, array, defaults, digest, calibration_version,
    parameters, model_report, calibration_report, readiness, fit_extrinsics, board_world,
    observation, locate, build_plan, ManipulationRobot, project_to_plane, inside, robot_description,
    camera_pose, projection_offset, reach_range)

ROOT=Path(__file__).resolve().parent


class Manipulation:
    PLAN_COMPUTE_SECONDS=45
    POST_STEP_TOLERANCE_DEG=3

    def __init__(self,demo,planner=None):
        self.demo,self.control=demo,demo.control
        self.path=demo.config_path.parent/'manipulation_setup.json'
        self.frames=self.path.parent/'logs/manipulation/frames'
        self.drafts={}
        self.planner=planner
        self.planning_lock=threading.Lock()
        self.planning_info=None
        self.preview_revision=0
        self._reference_cache=None
        self.control.manipulation=self
        self.control.extra_audit_paths.extend(ROOT/n for n in
            ('manipulation_geometry.py','manipulation_task.py','manipulation.html',
             'manipulation_ui.js','manipulation_view.js','cad_geometry.py','planning_worker.py',
             'manipulation_reference.py','manipulation_ik.py','manipulation_guided.py','manipulation_collision.py'))
        self.control.extra_audit_paths.extend([ASSETS/'manifest.json'])
        self.control.extra_audit_paths.extend([ASSETS/'cad_profile.json'])
        self.control.extra_audit_paths.extend([ROOT/'models/course/dofbot.urdf',ASSETS/'dofbot_display.urdf'])
        if self.path.exists():self.control.extra_audit_paths.append(self.path)
        from charuco_task import Calibration
        self.calibration = Calibration(self)

    def load(self):
        cfg=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else defaults()
        cfg.setdefault('cad',None)
        return cfg

    def sha(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    def status(self):
        with self.control.lock:
            cfg=self.load()
            events,_=self.demo.store.latest()
            recent=[{k:e.get(k) for k in ('task_id','status','result','started_at','mode','error')}
                    for e in events if e.get('operation')=='manipulation' and 'target' in e.get('plan',{})][-20:]
            return dict(setup=cfg,setup_sha256=self.sha(),mode='real' if self.control.real else 'simulation',
                readiness=readiness(cfg,self.control.config()),execution_readiness=readiness(cfg,self.control.config(),
                    self.control.real or cfg.get('cad') is None),
                drafts=[{k:d[k] for k in ('id','next_step','total','failed','real','xy_mm')} for d in self.drafts.values()],
                job=self.control.job,planning=self.planning_status(),nine_point_reference=self.reference_status(cfg),position_check=self.calibration.position_result_status(self.calibration.load()),recent_tasks=recent,mesh_manifest=json.loads((ASSETS/'manifest.json').read_text()),
                saved_observation_states=self.control.states.status()['states'],
                message='软件入口已就绪不代表完成标定；仅真实证据可登记现场验收')

    def invalidate(self):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            self.preview_revision+=1
            return dict(invalidated=True)

    def planning_reference(self,cfg):
        teaching=getattr(self.demo,'teaching',None)
        if cfg.get('cad') is None or teaching is None:return None
        setup=teaching.load()
        if setup.get('mapping_mode')!='reported_cards':return None
        # Revalidate the authoritative photos, buttons and configuration. Never
        # accept a caller-provided reference or silently use stale teaching data.
        teaching.bound(setup)
        from manipulation_reference import build_reference
        version=digest(dict(setup=setup,projection=calibration_version(cfg)))
        if self._reference_cache is None or self._reference_cache[0]!=version:
            self._reference_cache=(version,build_reference(cfg,self.control.config(),setup))
        return copy.deepcopy(self._reference_cache[1])

    def reference_status(self,cfg):
        try:
            reference=self.planning_reference(cfg)
            if reference is None:return dict(available=False,message='未建立可用九点参考；使用原几何求解')
            return dict(available=True,sample_count=9,version=reference['version'],baseline_frame_id=reference['baseline_frame_id'],
                message='已接入旧九点；按当前标定统一坐标，候选修正与全路径检查仍保留')
        except (ValueError,OSError,KeyError,TypeError) as exc:return dict(available=False,error=str(exc),message='九点资料待核对，生成计划将拒绝使用失配资料')

    def evidence(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('无效冻结画面编号')
        meta_path=self.frames/(identifier+'.json');image_path=self.frames/(identifier+'.jpg')
        meta=json.loads(meta_path.read_text())
        if meta.get('id')!=identifier:raise ValueError('冻结图像编号与元数据不符')
        if hashlib.sha256(image_path.read_bytes()).hexdigest()!=meta['jpeg_sha256']:raise ValueError('冻结图像已改变')
        for path in (meta_path,image_path):
            if path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(path)
        return meta

    def capture(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            self.preview_revision+=1
            self.control.audit(dict(operation='manipulation_capture',moves=0))
            with self.control.hardware():
                rows=self.control.read_rows(self.control.config())
                # 只借用原相机取帧方法，不使用原target或任何识别函数。
                frame,jpeg,sequence,captured=AutoTask.frame(self)
                after=self.control.read_rows(self.control.config())
                if rows!=after:raise ValueError('采样期间读数变化，请停稳后重新采样')
            identifier=uuid.uuid4().hex
            meta=dict(id=identifier,joints=rows,image_size=[frame.shape[1],frame.shape[0]],
                camera_sequence=sequence,captured_at=captured,real=self.control.real,
                jpeg_sha256=hashlib.sha256(jpeg).hexdigest())
            self.frames.mkdir(parents=True,exist_ok=True)
            (self.frames/(identifier+'.jpg')).write_bytes(jpeg)
            (self.frames/(identifier+'.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
            self.evidence(identifier)
            return dict(**meta,jpeg_base64=base64.b64encode(jpeg).decode(),
                link_matrices=[m.tolist() for m in cad_geometry.link_matrices(self.load(),[r['angle'] for r in rows[:5]])],
                scene=self.scene(),reach_overlay=self.reach_overlay([r['angle'] for r in rows[:5]],meta['image_size']))

    def reach_overlay(self,angles,size):
        """冻结画面上的方块中心伸展范围（模型上限，未计碰撞），仅供标注参考。"""
        try:
            cfg=self.load()
            if not cad_geometry.dynamic_capture(cfg):return None
            limits=reach_range(cfg,self.control.config(),cfg['table_z_mm']+cfg['grasp_height_mm'])
            if limits is None:return None
            k,d,r,t=camera_pose(cfg,angles);z=cfg['table_z_mm']+cfg['block_size_mm'][2]+projection_offset(cfg)
            lines=[]
            for radius in limits:
                theta=np.radians(np.arange(0,360.5,1.5))
                world=np.column_stack([radius*np.cos(theta),radius*np.sin(theta),np.full(len(theta),z)])
                camera=world@r.T+t
                # 只投影视野内的点，避免畸变模型在视野外折返。
                visible=(camera[:,2]>1)&(abs(camera[:,0]/np.maximum(camera[:,2],1))<size[0]/k[0,0]*.75)&(abs(camera[:,1]/np.maximum(camera[:,2],1))<size[1]/k[1,1]*.75)
                pixels=cv2.projectPoints(world,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
                current=[]
                for ok,(u,v) in zip(visible,pixels):
                    if ok and -50<=u<=size[0]+50 and -50<=v<=size[1]+50:current.append([float(u),float(v)])
                    elif current:
                        if len(current)>1:lines.append(dict(radius_mm=radius,points=current))
                        current=[]
                if len(current)>1:lines.append(dict(radius_mm=radius,points=current))
            return dict(radii_mm=list(limits),lines=lines,note='模型上限，未计碰撞，仅供参考')
        except (ValueError,KeyError,TypeError,cv2.error):return None

    def check_evidence(self,cfg):
        if cfg.get('cad') is not None:
            self.calibration.verify_applied(cfg)
            cad_geometry.validate(cfg)
            anchor=self.evidence(cfg['cad']['anchor_frame'])
            if anchor['real']!=self.control.real or anchor['image_size']!=cfg['camera']['image_size']:
                raise ValueError('CAD观察记录与当前模式或分辨率不符')
            if [r['angle'] for r in anchor['joints'][:5]]!=cfg['observation_readings']:
                raise ValueError('CAD相机位姿与原始读数证据不符')
            return anchor
        anchor=self.evidence(cfg['camera']['anchor_frame'])
        if anchor['real'] is not True:raise ValueError('模拟采样不能作为现场标定证据')
        if anchor['image_size']!=cfg['camera']['image_size']:raise ValueError('标定分辨率与证据不符')
        observation(cfg,self.control.config(),anchor['joints'])
        if [r['angle'] for r in anchor['joints'][:5]]!=cfg['observation_readings']:raise ValueError('观察原始读数与证据不符')
        solved=fit_extrinsics(cfg,cfg['board']['anchor_pixels'])
        if solved!=cfg['camera']['base_to_camera']:raise ValueError('外参与原始点选证据不一致，请重新保存观察标定')
        for sample in cfg['camera']['samples']:
            meta=self.evidence(sample['frame_id'])
            if meta['real'] is not True or meta['image_size']!=anchor['image_size']:raise ValueError('内参采样证据无效')
        if len(cfg['camera']['samples'])<10:raise ValueError('内参须保留至少10幅多视角点选证据')
        certificate=digest(dict(points_mm=cfg['board']['points_mm'],**{k:cfg['camera'][k] for k in ('samples','matrix','distortion','image_size')}))
        if cfg['camera'].get('intrinsics_certificate')!=certificate:raise ValueError('内参与采样或纸张尺寸不匹配，请重新计算内参')
        for check in cfg['checks']:
            meta=self.evidence(check['frame_id'])
            if meta['real'] is not True or meta['image_size']!=anchor['image_size']:raise ValueError('检查点证据无效')
            observation(cfg,self.control.config(),meta['joints'])
            if check['kind'] not in ('table','top'):raise ValueError('无效检查点类型')
            if np.min(np.linalg.norm(board_world(cfg)[:,:2]-array(check['xy_mm'],(2,),'检查坐标'),axis=1))<1:
                raise ValueError('独立检查不能使用拟合标定角点')
        for field in ('measurements','independent_measurements'):
            for sample in cfg['model'][field]:
                meta=self.evidence(sample['frame_id'])
                if meta['real'] is not True:raise ValueError('模型核对需要实物证据')
                q=array(sample['angles'],(5,),'明确关节目标')
                if np.max(abs(q-[r['angle'] for r in meta['joints'][:5]]))>1:raise ValueError('模型姿态与原始读数不符')
        return anchor

    def check_path_evidence(self,cfg):
        if cad_geometry.dynamic_capture(cfg):return
        for row in cfg['path_validations']:
            identifier=row.get('draft_id')
            if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('无效路径证据编号')
            path=self.frames.parent/(identifier+'.validation.json')
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=row.get('sha256'):raise ValueError('路径验证证据已改变')
            record=json.loads(data)
            if (record['version']!=calibration_version(cfg) or not record['real'] or record['failed']
                    or record['next_step']!=record['total'] or len(record['receipts'])!=record['total']
                    or any(not r['real'] for r in record['receipts'])):raise ValueError('路径验证证据不完整')
            if path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(path)

    def save(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            if fields.get('expected_sha256')!=self.sha():raise ValueError('配置已改变，请刷新后编辑')
            cfg=copy.deepcopy(fields.get('setup'));old=self.load()
            if isinstance(cfg,dict):cfg.setdefault('cad',None)
            if not isinstance(cfg,dict) or set(cfg)!=set(defaults()) or cfg.get('version')!=1:raise ValueError('无效完整抓放配置结构')
            for flag in ('model_reviewed','calibration_reviewed'):
                if type(cfg.get(flag)) is not bool:raise ValueError('核对状态须为布尔值')
            # 验证记录只能由真实逐步执行入口产生，不能通过编辑JSON填入。
            if cfg.get('path_validations')!=old['path_validations']:raise ValueError('不能手工填入路径验证记录')
            changed=calibration_version(cfg)!=calibration_version(old)
            if changed:
                cfg.update(model_reviewed=False,calibration_reviewed=False,arm_config_sha256=None,path_validations=[])
                self.drafts.clear()
                if cfg.get('cad') is not None:
                    before=copy.deepcopy(old);after=copy.deepcopy(cfg)
                    for data in (before,after):
                        if isinstance(data.get('cad'),dict):data['cad']['assumptions_confirmed']=False
                    if calibration_version(before)!=calibration_version(after):cfg['cad']['assumptions_confirmed']=False
            if cfg.get('cad') is not None:
                if cfg['model_reviewed'] or cfg['calibration_reviewed']:raise ValueError('CAD候选不能登记为实测模型或相机标定')
                cad_geometry.validate(cfg);parameters(cfg,self.control.config());self.check_evidence(cfg)
                cfg['arm_config_sha256']=arm_fingerprint(self.control.config())
            elif not changed and (cfg['model_reviewed'] or cfg['calibration_reviewed']):
                parameters(cfg,self.control.config())
                self.check_evidence(cfg)
                if cfg['model_reviewed']:model_report(cfg,self.control.config());self.check_meshes()
                if cfg['calibration_reviewed']:calibration_report(cfg)
                cfg['arm_config_sha256']=arm_fingerprint(self.control.config())
            self._save(cfg,'save_manipulation_setup')
            return self.status()

    def _save(self,cfg,operation):
        # 已持有control.lock。原数据留备份，临时文件原子替换。
        self.control.audit(dict(operation=operation,moves=0,setup=cfg))
        if self.path.exists():
            self.path.with_name(self.path.stem+'.before_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f')+'.json').write_bytes(self.path.read_bytes())
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');temp.replace(self.path)
        if self.path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(self.path)

    def check_meshes(self):
        manifest=json.loads((ASSETS/'manifest.json').read_text())
        p=cad_geometry.profile()
        if p['mesh_sha256']!={m['file']:m['sha256'] for m in manifest['meshes']}:raise ValueError('CAD候选与网格资源版本不符')
        for item in manifest['meshes']:
            if hashlib.sha256((ASSETS/'meshes'/item['file']).read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('三维结构文件版本已改变')
        return manifest

    def prepare_cad(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();meta=self.evidence(fields.get('frame_id'))
            if meta['real']!=self.control.real:raise ValueError('冻结画面与当前模式不同')
            self.calibration.verify_applied(cfg)
            self.check_meshes()
            candidate=cad_geometry.prepare(cfg,self.control.config(),meta,fields.get('observation_angles'),
                fields.get('intrinsics_recipe'),fields.get('image_roll_deg',0),fields.get('capture_mode','fixed_observation'))
            candidate=self.calibration.preserve_applied(cfg,candidate)
            return self.save(dict(setup=candidate,expected_sha256=fields.get('expected_sha256')))

    def intrinsics(self,fields):
        with self.control.lock:
            self.control.idle();cfg=copy.deepcopy(fields['setup'])
            samples=fields.get('samples');points=cfg['board']['points_mm']
            if not isinstance(samples,list) or not 10<=len(samples)<=30:raise ValueError('请采集10～30幅不同倾斜角度的标定纸画面')
            xy=array(points,(len(points),2),'纸上角点坐标')
            if len(xy)<4 or np.linalg.matrix_rank(xy-xy.mean(axis=0))<2:raise ValueError('至少四个非共线角点')
            obj=np.column_stack([xy,np.zeros(len(xy))]).astype(np.float32)
            images=[];size=cfg['camera']['image_size'];ids=set()
            for sample in samples:
                meta=self.evidence(sample['frame_id'])
                if meta['real'] is not True or meta['image_size']!=size:raise ValueError('内参采样须为相同分辨率的真实画面')
                if meta['id'] in ids:raise ValueError('同一幅画面不能重复作为多视角采样')
                ids.add(meta['id']);images.append(array(sample['pixels'],(len(xy),2),'角点像素').astype(np.float32))
            guess=np.array([[size[0],0,size[0]/2],[0,size[0],size[1]/2],[0,0,1.]],float)
            rms,k,d,rvecs,_=cv2.calibrateCamera([obj]*len(images),images,tuple(size),guess,None,flags=cv2.CALIB_USE_INTRINSIC_GUESS)
            normals=np.array([cv2.Rodrigues(r)[0][:,2] for r in rvecs])
            if rms>1 or np.linalg.matrix_rank(normals-normals.mean(axis=0),tol=.01)<2:raise ValueError('误差超过1像素或倾斜视角不足，请补采不同视角')
            cfg['camera'].update(matrix=k.tolist(),distortion=d.reshape(-1).tolist(),samples=samples)
            cfg['camera']['intrinsics_certificate']=digest(dict(points_mm=points,**{key:cfg['camera'][key] for key in ('samples','matrix','distortion','image_size')}))
            return dict(camera=cfg['camera'],rms_px=float(rms))

    def anchor(self,fields):
        with self.control.lock:
            cfg=copy.deepcopy(fields['setup']);meta=self.evidence(fields.get('frame_id'))
            if not meta['real']:raise ValueError('模拟画面不能建立实机观察标定')
            cfg['camera']['anchor_frame']=meta['id'];cfg['camera']['image_size']=meta['image_size']
            cfg['observation_readings']=[r['angle'] for r in meta['joints'][:5]]
            cfg['camera']['base_to_camera']=fit_extrinsics(cfg,cfg['board']['anchor_pixels'])
            return self.save(dict(setup=cfg,expected_sha256=fields.get('expected_sha256')))

    def target(self,cfg,value):
        meta=self.evidence(value.get('frame_id'))
        if meta['image_size']!=value.get('image_size'):raise ValueError('目标与冻结画面分辨率不符')
        if meta['real']!=self.control.real:raise ValueError('冻结画面与当前控制模式不符')
        if int(time.time()*1000)-meta['captured_at']>120000:raise ValueError('冻结画面超过120秒，请重新冻结并选点')
        observation(cfg,self.control.config(),meta['joints'])
        safe=copy.deepcopy(value)
        if cfg.get('cad') is not None:safe['_frame_joint_angles']=[r['angle'] for r in meta['joints'][:5]]
        if cad_geometry.dynamic_capture(cfg):safe['_start_joint_angles']=[r['angle'] for r in meta['joints']]
        return locate(cfg,safe),meta

    def destination(self,fields):
        with self.control.lock:
            self.control.idle();cfg=self.load();meta=self.evidence(fields.get('frame_id'))
            if not meta['real'] or not self.control.real:raise ValueError('固定点须使用真实观察图像')
            observation(cfg,self.control.config(),meta['joints'])
            point=project_to_plane(cfg,fields.get('pixel'),cfg['table_z_mm'],
                [r['angle'] for r in meta['joints'][:5]] if cfg.get('cad') is not None else None)
            block=np.asarray(cfg['block_size_mm'])
            if not inside(cfg,point[:2],np.linalg.norm(block[:2])/2+cfg['max_position_error_mm']):raise ValueError('固定点超出取放区域')
            return dict(xy_mm=point[:2].tolist())

    def planning_status(self):
        info=self.planning_info
        if not info:return None
        stage=info['stage']
        path=info.get('progress_file')
        if info['status']=='running' and path:
            try:
                for line in Path(path).read_text(errors='replace').splitlines()[-6:]:
                    try:stage=json.loads(line).get('stage',stage)
                    except ValueError:pass
            except OSError:pass
        return dict(id=info['id'],status=info['status'],stage=stage,error=info.get('error'),
            elapsed_seconds=round(info.get('elapsed_seconds',time.monotonic()-info['started']),1))

    def compute_plan(self,cfg,arms,value,require_path,reference=None):
        directory=self.frames.parent/'planning';directory.mkdir(parents=True,exist_ok=True)
        identifier=self.planning_info['id']
        payload=dict(setup=cfg,arms=arms,target=value,require_path=require_path)
        if reference is not None:payload['reference']=reference
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        (directory/(identifier+'.request.json')).write_text(raw,encoding='utf-8')
        progress=directory/(identifier+'.progress.jsonl')
        self.planning_info.update(stage='本地逆解与扫掠检查',progress_file=str(progress))
        environment=os.environ.copy()
        for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):environment[name]='1'
        try:
            with progress.open('w') as log:
                run=subprocess.run([sys.executable,'-B',str(ROOT/'planning_worker.py')],input=raw,text=True,
                    stdout=subprocess.PIPE,stderr=log,env=environment,cwd=str(ROOT),timeout=self.PLAN_COMPUTE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            stage=self.planning_status()['stage']
            raise ValueError('计划计算超时（纯计算上限'+str(self.PLAN_COMPUTE_SECONDS)+'秒；'+stage+'）。未生成可执行计划，未发送运动指令。') from exc
        try:result=json.loads(run.stdout)
        except ValueError:raise ValueError('纯计算进程未返回有效结果；未生成运动计划')
        if run.returncode or result.get('error'):raise ValueError(result.get('error','纯计算进程失败'))
        return result['plan']

    def preview(self,fields):
        if not self.planning_lock.acquire(blocking=False):raise ValueError('已有计划正在计算，请等待；未重复启动')
        self.planning_info=dict(id=uuid.uuid4().hex,status='running',stage='核对冻结画面与起始姿态',started=time.monotonic())
        try:
            with self.control.lock:
                self.control.idle();self.control.pending=None
                input_revision=self.preview_revision
                cfg=self.load();arms=self.control.config();dry=fields.get('dry_run') is True
                if not cad_geometry.dynamic_capture(cfg) and fields.get('scene_confirmed') is not True:raise ValueError('请确认底座、桌面、相机和焦距未变，以及现场路径与停止方式')
                self.check_meshes();self.check_evidence(cfg)
                if not dry:self.check_path_evidence(cfg)
                value=copy.deepcopy(fields.get('target'))
                if not isinstance(value,dict):raise ValueError('请先框选并点选目标')
                located,meta=self.target(cfg,value)
                if cfg.get('cad') is not None:value['_frame_joint_angles']=[r['angle'] for r in meta['joints'][:5]]
                if cad_geometry.dynamic_capture(cfg):value['_start_joint_angles']=[r['angle'] for r in meta['joints']]
                reference=self.planning_reference(cfg)
                audit_before=self.control.audit(dict(operation='manipulation_planning',moves=0))
                if self.control.real and arms.get('hardware_ready') is not True:raise ValueError('实机未就绪')
                with self.control.hardware():
                    rows=self.start_rows(cfg,arms,meta)
                require_path=not dry and (self.control.real or cfg.get('cad') is None)
            # Pure subprocess math never holds the servo/control lock. Status,
            # manual controls and calibration pages remain responsive.
            request=dict(operation='manipulation',setup=cfg,arms=arms,target=value,require_path=require_path)
            if reference is not None:request['reference']=reference
            remote=None
            parallel=reference is not None and self.planner is not None
            if parallel:
                # Both branches are pure computation on the same immutable
                # snapshot. Keep independent parity checks without serial delay.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    local_job=pool.submit(self.compute_plan,cfg,arms,value,require_path,reference)
                    ros_job=pool.submit(self.planner.request,request)
                    plan=local_job.result()
                    self.planning_info.update(stage='等待ROS2独立计算并核对',progress_file=None)
                    remote=ros_job.result()
            else:
                plan=self.compute_plan(cfg,arms,value,require_path,reference) if reference is not None else self.compute_plan(cfg,arms,value,require_path)
            self.planning_info.update(stage='核对本地与ROS2计划',progress_file=None)
            if self.planner is not None:
                if not parallel:remote=self.planner.request(request)
                if not isinstance(remote,dict):raise ValueError('ROS2未返回有效计划，未重复请求')
                compared=('steps','initial_angles','duration_ms','calibration_version','model_sha256')
                if any(remote.get(k)!=plan[k] for k in compared):raise ValueError('ROS2与本地运动参数不一致')
                if remote.get('reference')!=plan.get('reference'):raise ValueError('ROS2与本地九点参考或阶段候选不一致')
                if remote.get('target',{}).get('destination_kind')!=plan['target']['destination_kind']:raise ValueError('ROS2放置方式不一致')
                for k in ('xyz_mm','destination_xy_mm','edge_yaw_deg'):
                    if not np.allclose(remote.get('target',{}).get(k),plan['target'][k],rtol=0,atol=1e-8):raise ValueError('ROS2与本地空间目标不一致')
            with self.control.lock:
                self.control.idle()
                if input_revision!=self.preview_revision:raise ValueError('计算期间选点已修改或预览已撤销，请重新生成')
                if reference!=self.planning_reference(cfg):raise ValueError('计算期间九点参考已改变，请重新生成')
                args=dict(operation='manipulation',plan=plan,target=value,moves=len(plan['steps']),dry_run=dry,
                    plan_inputs=self.plan_inputs(cfg,arms,value,require_path,reference),plan_sha256=digest(plan))
                if reference is not None:args['reference']=reference
                audit=self.control.audit(args)
                if audit_before['hashes']!=audit['hashes']:raise ValueError('规划期间代码、配置或证据变化，请重新生成')
                # Other manual operations may run while CPU work is isolated.
                # A finished plan can only be offered from the same real pose.
                with self.control.hardware():
                    after=self.start_rows(cfg,arms,meta)
                    if any(a['read_ok'] and b['read_ok'] and abs(a['angle']-b['angle'])>1 for a,b in zip(after,rows)):
                        raise ValueError('计算期间机械臂姿态已改变，请重新冻结并生成计划')
                if dry:
                    identifier=uuid.uuid4().hex
                    self.drafts[identifier]=dict(id=identifier,plan=plan,target=value,rows=after,version=calibration_version(cfg),
                        audit_hashes=audit['hashes'],next_step=0,total=len(plan['steps']),failed=False,
                        real=self.control.real,xy_mm=located['xyz_mm'][:2],receipts=[],reference=reference)
                    response=dict(draft_id=identifier,plan=plan,dry_run=True,scene=self.scene(cfg))
                else:response=self.pending(args,after,audit,meta,scene=self.scene(cfg))
            self.planning_info.update(status='completed',stage='计划已生成，等待用户核对',elapsed_seconds=time.monotonic()-self.planning_info['started'])
            return response
        except Exception as exc:
            self.planning_info.update(status='failed',error=str(exc),stage='计算未生成可执行计划',elapsed_seconds=time.monotonic()-self.planning_info['started'])
            raise
        finally:self.planning_lock.release()

    @staticmethod
    def continue_read_failures(args):
        """用户2026-10-29要求：随臂完整抓放计划的个别读取失败只记录警告并继续。"""
        return (args.get('operation')=='manipulation' and not args.get('step_only') and not args.get('dry_run')
                and args.get('plan',{}).get('capture_mode')=='joint_snapshot_v1')

    def start_rows(self,cfg,arms,meta):
        """读取起点；随臂模式下失败关节只记录，成功关节须与冻结读数相差不超过1°。"""
        dynamic=cad_geometry.dynamic_capture(cfg)
        rows=self.control.read_rows(arms,allow_failed=dynamic)
        if not dynamic:
            observation(cfg,arms,rows);return rows
        if any(r['read_ok'] and abs(r['angle']-f['angle'])>1 for r,f in zip(rows,meta['joints'])):
            raise ValueError('机械臂已离开冻结时的姿态，请重新冻结并选点')
        return rows

    def pending(self,args,rows,audit,meta=None,**extra):
        token=secrets.token_urlsafe(24)
        self.control.pending=dict(token=token,expires=time.monotonic()+120,audit=audit,parameters=args,
            rows=rows,config_bytes=self.control.path.read_bytes(),frame_meta=meta)
        source=self.load().get('cad')
        conditions='仅执行列出的有限计划；确认冻结目标、放置空区域未改变，已核对线缆和停止方式；失败不重试、复位或松爪。'
        if cad_geometry.dynamic_capture(self.load()):
            conditions+=' 本次按冻结关节姿态从模型定位；确认夹爪空载，并采用本次模型及成像参数：'+source['intrinsics_source']['description']+'；实际精度未验收。'
        reference=args.get('plan',{}).get('reference')
        if reference:conditions+=' 九点引导来源 '+reference['version'][:8]+'：仅提供参考姿态，全部目标已重新计算并检查，本次真实抓放仍待现场验证。'
        warnings=args.get('plan',{}).get('warnings',[])
        if warnings:conditions+=' 定位诊断：'+'；'.join(warnings)
        return dict(token=token,expires_in=120,parameters=args,joints=rows,audit=audit,
            mode='real' if self.control.real else 'simulation',conditions=conditions,**extra)

    def step(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            draft=self.drafts.get(fields.get('draft_id'));cfg=self.load();arms=self.control.config()
            if not draft or draft['failed'] or draft['version']!=calibration_version(cfg):raise ValueError('空载验证计划已失效')
            if draft.get('reference')!=self.planning_reference(cfg):raise ValueError('空载计划的九点参考已变化')
            if self.control.audit(dict(operation='manipulation_step_check',moves=0))['hashes']!=draft['audit_hashes']:
                raise ValueError('空载计划生成后代码、规则或资料变化，请重新生成')
            index=draft['next_step']
            if fields.get('index')!=index or index>=draft['total']:raise ValueError('必须按顺序逐步验证')
            if fields.get('empty_confirmed') is not True:raise ValueError('空载验证须移走方块并确认夹爪空载')
            with self.control.hardware():rows=self.control.read_rows(arms)
            expected=draft['plan']['initial_angles'].copy()
            for s in draft['plan']['steps'][:index]:
                for joint,value in s['joints']:expected[joint-1]=value
            if any(abs(r['angle']-v)>1 for r,v in zip(rows,expected)):raise ValueError('起始读数不符合本步预期，不能跳步或自动恢复')
            step=draft['plan']['steps'][index]
            args=dict(operation='manipulation',step_only=True,draft_id=draft['id'],index=index,
                plan=dict(steps=[step]),moves=1,empty_confirmed=True)
            return self.pending(args,rows,self.control.audit(args))

    @staticmethod
    def plan_inputs(cfg,arms,target,require_path,reference):
        """计划输入指纹；代码、规则与证据文件另由审计散列核对。"""
        return digest(dict(setup=cfg,arms=arms,target=target,require_path=require_path,reference=reference))

    def validate_execution(self,p):
        args=p['parameters'];cfg=self.load()
        if args.get('step_only'):
            d=self.drafts.get(args['draft_id'])
            if not d or d['failed'] or d['next_step']!=args['index'] or d['version']!=calibration_version(cfg):raise ValueError('验证步骤已失效')
            if d.get('reference')!=self.planning_reference(cfg):raise ValueError('验证步骤的九点参考已变化')
            if self.control.audit(dict(operation='manipulation_step_check',moves=0))['hashes']!=d['audit_hashes']:
                raise ValueError('空载计划版本已改变')
            if args['plan']['steps']!=[d['plan']['steps'][args['index']]]:raise ValueError('验证步骤与原计划不一致')
        else:
            # 冻结目标重算一致性，绝不取新图或调用任何识别。
            self.check_evidence(cfg);self.check_meshes();self.check_path_evidence(cfg)
            if cfg.get('cad') is not None:
                frozen=self.evidence(args['target']['frame_id'])
                if args['target'].get('_frame_joint_angles')!=[r['angle'] for r in frozen['joints'][:5]]:
                    raise ValueError('相机投影姿态与冻结原始读数不一致')
                if cad_geometry.dynamic_capture(cfg) and args['target'].get('_start_joint_angles')!=[r['angle'] for r in frozen['joints']]:
                    raise ValueError('起始六关节读数与冻结证据不一致')
            require_path=self.control.real or cfg.get('cad') is None
            reference=self.planning_reference(cfg)
            if args.get('reference')!=reference:raise ValueError('九点参考在生成计划后变化，请重新生成')
            # 用户10-29要求即时执行：输入与审计散列一致即沿用预览时已完成全路径检查的计划，不重复计算。
            if (args.get('plan_inputs')!=self.plan_inputs(cfg,self.control.config(),args['target'],require_path,reference)
                    or args.get('plan_sha256')!=digest(args['plan'])):raise ValueError('冻结目标与计划不一致')

    def run(self,controller,p):
        args=p['parameters'];self.validate_execution(p)  # 仅核对指纹，毫秒级
        steps=args['plan']['steps']
        if p.get('frame_meta') and int(time.time()*1000)-p['frame_meta']['captured_at']>120000:raise ValueError('冻结目标过期')
        for s in steps:
            for i,v in s['joints']:arm.angle(controller.config,i,v)
            arm.timing(dict(controller.config,motion_ms=s['motion_ms'],settle_ms=s['settle_ms']))
            lead=s.get('blend_next_ms')
            if lead is not None and (type(lead) is not int or not 0<lead<s['motion_ms']):raise ValueError('衔接提前量无效')
        if not args.get('step_only') and steps and steps[-1].get('blend_next_ms') is not None:raise ValueError('最后一步不能衔接')
        tolerate=self.continue_read_failures(args)
        if tolerate:
            # 起点以冻结读数为准；成功读取的关节已在执行前核对不超过1°。
            expected=list(args['plan']['initial_angles'])
            if any(r['read_ok'] and abs(r['angle']-v)>1 for r,v in zip(p['rows'],expected)):raise ValueError('执行前读数与冻结起点相差超过1度')
        else:expected=[r['angle'] for r in p['rows']]
        receipts=[];sent=0;warnings=[];holding=None
        task=dict(task_id=p['token'],mode='real' if controller.real else 'simulation',color='manual',
            operation='manipulation',destination_kind=args.get('plan',{}).get('target',{}).get('destination_kind'),
            started_at=arm.now(),status='running',result='unconfirmed',retry_count=0,plan=args['plan'],target=args.get('target'))
        self.demo.store.append(task)
        try:
            for index,s in enumerate(steps):
                if self.control.audit(args)['hashes']!=p['audit']['hashes']:raise ValueError('执行期间规则、代码或配置变化')
                self.control.record('manipulation_sending',dict(id=p['token'],step=index,command=s))
                controller.config=dict(controller.config,motion_ms=s['motion_ms'],settle_ms=s['settle_ms'])
                previous_gripper=expected[5]
                blend=tolerate and s.get('blend_next_ms') is not None
                # 用户10-30：单关节写入失败重发同一绝对目标最多2次，逐次记录。
                try:
                    if blend:
                        # 上一段结束前下发下一段，不停稳、不回读；切角路径已在规划时检查。
                        controller.write([tuple(pair) for pair in s['joints']],hold_ms=s['motion_ms']-s['blend_next_ms'],retries=arm.MAX_WRITE_RETRIES)
                    else:controller.write([tuple(pair) for pair in s['joints']],retries=arm.MAX_WRITE_RETRIES)
                finally:
                    for retry in getattr(controller,'last_retries',[]):
                        self.control.record('write_retry',dict(id=p['token'],step=index,**retry))
                        warnings.append(dict(step=index,kind='write_retry',**retry))
                sent+=1
                for i,v in s['joints']:expected[i-1]=v;self.control.commanded[str(i)]=v
                if blend:
                    receipts.append(dict(step=index,expected=expected.copy(),readings=None,real=controller.real,verified=False,blended=True,warnings=[]))
                    with self.control.lock:self.control.job.update(sent=sent,verified_steps=sum(r['verified'] for r in receipts),total=len(steps),read_warnings=len(warnings))
                    continue
                if s['label']=='夹持方块':holding=sorted((previous_gripper,expected[5]))
                elif s['label']=='释放方块':holding=None
                rows=self.control.read_rows(controller.config,allow_failed=tolerate)
                step_warnings=[]
                for r,v in zip(rows,expected):
                    if not r['read_ok']:
                        step_warnings.append(dict(step=index,joint=r['id'],error=r.get('error'),kind='read_failed'));continue
                    if abs(r['angle']-v)<=1:continue
                    if tolerate and r['id']==6 and holding and holding[0]-1<=r['angle']<=holding[1]+1:
                        step_warnings.append(dict(step=index,joint=6,angle=r['angle'],expected=v,kind='gripper_blocked'));continue
                    # 用户10-29要求：负载下未完全到位≤3°记警告继续，该步不计验证。
                    if tolerate and abs(r['angle']-v)<=self.POST_STEP_TOLERANCE_DEG:
                        step_warnings.append(dict(step=index,joint=r['id'],angle=r['angle'],expected=v,kind='deviation'));continue
                    raise ValueError('运动后关节'+str(r['id'])+'读数'+str(r['angle'])+'°与目标'+str(v)+'°偏差超过'+
                        str(self.POST_STEP_TOLERANCE_DEG if tolerate else 1)+'度')
                warnings.extend(step_warnings)
                verified=not any(w['kind'] in ('read_failed','deviation') for w in step_warnings)
                receipts.append(dict(step=index,expected=expected.copy(),readings=rows,real=controller.real,verified=verified,warnings=step_warnings))
                with self.control.lock:self.control.job.update(sent=sent,verified_steps=sum(r['verified'] for r in receipts),total=len(steps),read_warnings=len(warnings))
            if args.get('step_only'):
                d=self.drafts[args['draft_id']];d['receipts'].extend(receipts);d['next_step']+=1
            verified_steps=sum(r['verified'] for r in receipts)
            task.update(status='completed',ended_at=arm.now(),instructions_sent=sent,verified_steps=verified_steps,receipts=receipts,read_warnings=warnings)
            self.demo.store.append(task)
            blended=sum(1 for r in receipts if r.get('blended'))
            note=('；读数/写入警告'+str(len(warnings))+'条（读取失败或偏差的步骤未计为验证，写入重发已记录）' if warnings else '')+('；衔接段'+str(blended)+'处未停稳回读' if blended else '')
            return dict(status='completed',task_id=p['token'],sent=sent,verified_steps=verified_steps,read_warnings=warnings,note=note,
                message=('本步空载下发与回读完成；仍需现场观察' if args.get('step_only') else
                ('夹取抬升计划下发与回读完成；保持夹持，结果待人工确认' if args['plan']['target'].get('task_mode')=='pickup' else '完整计划下发与回读完成，夹取及放置结果待人工确认'))+note)
        except Exception as exc:
            if args.get('step_only'):self.drafts[args['draft_id']]['failed']=True
            task.update(status='failed',ended_at=arm.now(),error=str(exc),instructions_sent=sent,verified_steps=sum(r['verified'] for r in receipts),receipts=receipts,read_warnings=warnings)
            self.demo.store.append(task)
            raise

    def validation(self,fields):
        with self.control.lock:
            self.control.idle();self.control.pending=None
            cfg=self.load();d=self.drafts.get(fields.get('draft_id'))
            if (not d or d['failed'] or not d['real'] or d['next_step']!=d['total']
                    or d['version']!=calibration_version(cfg) or fields.get('confirmed') is not True):
                raise ValueError('须完成真实空载逐步执行并现场确认，模拟或未完成步骤不能登记')
            if any(x.get('draft_id')==d['id'] for x in cfg['path_validations']):raise ValueError('该验证已登记')
            if self.control.audit(dict(operation='manipulation_validation_check',moves=0))['hashes']!=d['audit_hashes']:
                raise ValueError('验证期间规则、代码或资料变化，不能登记')
            evidence=self.frames.parent/(d['id']+'.validation.json')
            evidence.parent.mkdir(parents=True,exist_ok=True)
            encoded=json.dumps(d,ensure_ascii=False,allow_nan=False).encode('utf-8')
            if evidence.exists():
                if evidence.read_bytes()!=encoded:raise ValueError('已有路径证据内容不同，保留原文件，未覆盖')
            else:
                with evidence.open('xb') as output:output.write(encoded)
            cfg['path_validations'].append(dict(draft_id=d['id'],version=d['version'],real=True,passed=True,
                xy_mm=d['xy_mm'],yaw_deg=d['plan']['target']['edge_yaw_deg'],sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),at=arm.now()))
            self._save(cfg,'record_manipulation_empty_validation')
            return self.status()

    def result(self,fields):
        with self.control.lock:
            self.control.idle()
            events,_=self.demo.store.latest()
            task=next((copy.deepcopy(e) for e in events if e.get('task_id')==fields.get('task_id')),None)
            if not task or task.get('operation')!='manipulation' or task.get('status')!='completed':raise ValueError('没有已完成的本次抓放任务')
            if 'target' not in task.get('plan',{}):raise ValueError('空载步骤不能计为完整抓放结果')
            if fields.get('result') not in ('success','failed'):raise ValueError('请选择人工观察成功或失败')
            task.update(result=fields['result'],operator_confirmed=True,operator_note=str(fields.get('note',''))[:1000],result_at=arm.now())
            self.demo.store.append(task)
            return dict(recorded=True,task_id=task['task_id'],result=task['result'],acceptance=self.acceptance())

    def acceptance(self):
        events,_=self.demo.store.latest();out={}
        for kind in ('fixed','pixel'):
            trials=[e for e in events if e.get('operation')=='manipulation' and e.get('mode')=='real'
                and e.get('destination_kind')==kind and 'target' in e.get('plan',{})]
            trials=[e for e in trials if e['plan'].get('calibration_version')==calibration_version(self.load())]
            trials.sort(key=lambda e:e['started_at'])
            warmup=next((i+3 for i in range(len(trials)-2) if all(x.get('result')=='success' for x in trials[i:i+3])),None)
            series=[] if warmup is None else trials[warmup:warmup+10]
            success=sum(e.get('result')=='success' for e in series)
            out[kind]=dict(trials=len(trials),warmup_passed=warmup is not None,series_count=len(series),success=success,
                passed=len(series)==10 and success>=8 and all(e.get('result') in ('success','failed') or e.get('status')=='failed' for e in series))
        return out

    def scene(self,cfg=None):
        cfg=cfg or self.load()
        return dict(table_z_mm=cfg['table_z_mm'],workspace_xy_mm=cfg['workspace_xy_mm'],motion_bounds_mm=cfg['motion_bounds_mm'],
            block_size_mm=cfg['block_size_mm'],obstacles=cfg['obstacles'],model=cfg['model'],
            cad=cfg.get('cad'),
            meshes=[dict(file=m['file'],url='/manipulation/assets/'+m['file']) for m in json.loads((ASSETS/'manifest.json').read_text())['meshes']])
