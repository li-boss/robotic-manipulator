"""十张示教卡片的持久化资料；拍照复用既有只读采样，保存不运动。"""
import copy
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re

import numpy as np
import cv2
from core import arm
from teaching_plan import digest, prerequisites, fit, reported_mode
from saved_states import checked as checked_state

LABELS = ['空桌面观察', '左上', '上中', '右上', '左中', '中心', '右中', '左下', '下中', '右下']


class TeachingCards:
    def __init__(self, demo):
        self.demo, self.control, self.teaching = demo, demo.control, demo.teaching
        self.path = demo.config_path.parent / 'teaching_cards.json'
        self.control.extra_audit_paths.extend(Path(__file__).parent / name for name in ['teaching_cards.py', 'cards_ui.js'])
        if self.path.exists():self.control.extra_audit_paths.append(self.path)

    def raw(self):return self.path.read_bytes() if self.path.exists() else b''
    def sha(self):return hashlib.sha256(self.raw()).hexdigest()
    def load(self):
        raw = self.raw()
        cfg = json.loads(raw.decode('utf-8-sig')) if raw else dict(version=1, slots=[{} for _ in LABELS])
        if cfg.get('version') != 1 or not isinstance(cfg.get('slots'), list) or len(cfg['slots']) != 10:
            raise ValueError('十卡片数据格式异常')
        return cfg

    def photo(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('照片编号无效')
        self.demo.auto.snapshot_metadata(identifier)
        return (self.demo.auto.samples / (identifier + '.jpg')).read_bytes()

    def reported_source(self, setup):
        """读取已有照片与明确目标；不采样、不读取或写入舵机。"""
        arms=self.control.config();prerequisites(setup,arms)
        if setup.get('task_mode')!='pickup_only':raise ValueError('九点卡片映射仅用于抓取保持模式')
        self.teaching.evidence(setup['baseline']['frame_id'])
        states=self.control.states.status();by_id={s['id']:s for s in states['states']}
        samples=[]
        for index,slot in enumerate(self.load()['slots'][1:],1):
            target=slot.get('target')
            if not target or slot.get('center_confirmed') is not True:raise ValueError(LABELS[index]+'缺少已有定位照片或中心确认')
            if target.get('baseline_id')!=setup['baseline']['frame_id'] or target.get('arm_config_sha256')!=digest(arms):
                raise ValueError(LABELS[index]+'定位资料与当前观察基准或配置不匹配')
            meta=self.teaching.evidence(target['frame_id'])
            if meta.get('teaching_kind')!='target' or meta.get('baseline_id')!=setup['baseline']['frame_id'] or meta.get('arm_config_sha256')!=digest(arms):
                raise ValueError('定位照片元数据不匹配')
            photo=self.photo(target['frame_id']);image=cv2.imdecode(np.frombuffer(photo,np.uint8),cv2.IMREAD_COLOR)
            if image is None or [image.shape[1],image.shape[0]]!=setup['baseline']['image_size'] or meta.get('image_size')!=setup['baseline']['image_size']:
                raise ValueError('定位照片缺失、损坏或分辨率改变')
            pixel=slot.get('pixel',target.get('pixel'))
            if not isinstance(pixel,list) or len(pixel)!=2 or any(not arm.number(v) for v in pixel) or np.linalg.norm(np.asarray(pixel)-meta['target']['pixel'])>2:
                raise ValueError('已确认中心与原定位照片不一致')
            sample=dict(slot=index,name=LABELS[index],pixel=pixel,target_frame=target['frame_id'],
                        descriptor=meta['target']['descriptor'],photo_sha256=hashlib.sha256(photo).hexdigest(),
                        metadata_sha256=hashlib.sha256((self.demo.auto.samples/(target['frame_id']+'.json')).read_bytes()).hexdigest(),
                        stage_orders={},stage_motion_ms={})
            for phase,key in [('above','open'),('grasp','closed')]:
                saved=slot.get('plans',{}).get(phase)
                if not saved or saved.get('id') not in by_id:raise ValueError(LABELS[index]+'缺少明确的'+phase+'目标按钮')
                plan=checked_state(arms,saved)
                if plan!=checked_state(arms,by_id[saved['id']]):raise ValueError('卡片目标与已保存姿态不同')
                if plan['teaching_stage']!=phase or plan['angles'][5]!=arms['gripper'][key]:raise ValueError('阶段或夹爪目标不匹配')
                if phase=='grasp' and plan['order'][-1]!=6:raise ValueError('抓取顺序必须最后执行6号夹爪')
                sample[phase]=plan['angles'][:5];sample['stage_orders'][phase]=plan['order'];sample['stage_motion_ms'][phase]=plan['motion_ms']
            samples.append(sample)
        return dict(source_type='user_report',operator_report=True,
                    report='用户报告九个固定位置已经实际验证抓取；新位置为插值试抓',
                    cards_sha256=self.sha(),states_sha256=states['sha256'],samples=samples)

    def fit_reported(self, fields):
        with self.control.lock, self.transaction():
            self.control.idle()
            if fields.get('expected_cards_sha256')!=self.sha() or fields.get('expected_sha256')!=self.teaching.sha():
                raise ValueError('卡片或示教配置已变化，请刷新')
            if fields.get('user_reported_verified') is not True or fields.get('scene_confirmed') is not True:
                raise ValueError('请明确报告九点抓取已验证，并确认底座、桌面和相机安装未改变')
            setup=self.teaching.load();source=self.reported_source(setup)
            setup['mapping_mode']='reported_cards';setup['reported_mapping']=copy.deepcopy(source)
            setup['reported_mapping']['fit']=fit(setup,self.control.config())
            # 构建每个单元的计划以预检顺序、时间和全部目标；不连接硬件。
            from teaching_plan import build
            for cell in setup['reported_mapping']['fit']['cells']:
                pixel=np.mean([source['samples'][i]['pixel'] for i in cell['indices']],axis=0).tolist()
                build(setup,self.control.config(),pixel)
            if self.reported_source(setup)!=source:raise ValueError('拟合期间来源资料改变，未保存')
            self.teaching.save(setup,fields['expected_sha256'],'fit_user_reported_nine_cards')
            self.teaching.drafts.clear()
            return dict(status=self.status(),teaching=self.teaching.status(),message='九点用户报告映射已建立，没有运动；新位置为插值试抓')

    def status(self):
        with self.control.lock:
            cfg = self.load();setup = self.teaching.load();baseline = setup['baseline']
            slots = copy.deepcopy(cfg['slots'])
            if not slots[0].get('reference') and baseline:
                slots[0].update(reference=dict(frame_id=baseline['frame_id'], angles=baseline['angles']+[None]), imported=True)
            for i, slot in enumerate(slots):
                slot['index'], slot['name'] = i, LABELS[i]
                target = slot.get('target')
                slot['stale'] = bool(target and (not baseline or target['baseline_id'] != baseline['frame_id']
                    or target['arm_config_sha256'] != digest(self.control.config())))
                slot['complete'] = bool(target and slot.get('above') and slot.get('grasp') and slot.get('center_confirmed') and not slot['stale'])
                slot['verified'] = any(s.get('target_frame') == target['frame_id'] and s.get('receipts') == {
                    'above':slot.get('above', {}).get('frame_id'), 'grasp':slot.get('grasp', {}).get('frame_id')}
                    for s in setup['samples']) if target else False
                slot['reported'] = bool(reported_mode(setup) and setup.get('reported_mapping',{}).get('cards_sha256')==self.sha()
                    and any(s.get('slot')==i for s in setup.get('reported_mapping',{}).get('samples',[])))
            history=[]
            if baseline:
                try:started=self.demo.auto.snapshot_metadata(baseline['frame_id'])['captured_at']
                except (ValueError,OSError):started=float('inf')
                for path in sorted(self.demo.auto.samples.glob('*.json')):
                    if not re.fullmatch('[0-9a-f]{32}',path.stem):continue
                    try:meta=json.loads(path.read_text(encoding='utf-8'))
                    except (ValueError,OSError):continue
                    if not isinstance(meta,dict) or meta.get('teaching_kind') not in ['target','above','grasp']:continue
                    if meta.get('arm_config_sha256')!=digest(self.control.config()) or meta.get('captured_at',0)<started:continue
                    if meta['teaching_kind']=='target' and meta.get('baseline_id')!=baseline['frame_id']:continue
                    history.append(dict(id=path.stem,kind=meta['teaching_kind'],captured_at=meta['captured_at']))
            history.sort(key=lambda m:m['captured_at'])
            return dict(sha256=self.sha(), slots=slots, teaching_sha256=self.teaching.sha(),legacy_frames=history)

    def import_frames(self, fields):
        with self.control.lock, self.transaction():
            self.control.idle();index=self.checked_slot(fields)
            if index==0:raise ValueError('观察卡片直接显示已保存的观察基准')
            cfg=self.load();setup=self.teaching.load();arms=digest(self.control.config())
            source=self.teaching.evidence(fields.get('target_frame'))
            if source.get('teaching_kind')!='target' or not setup['baseline'] or source.get('baseline_id')!=setup['baseline']['frame_id'] or source.get('arm_config_sha256')!=arms:
                raise ValueError('旧木块照片不属于当前观察基准和配置')
            slot=dict(plans=cfg['slots'][index].get('plans',{}),target=dict(frame_id=source['id'],captured_at=source['captured_at'],
                angles=None,readings=source['joints'],image_size=source['image_size'],pixel=source['target']['pixel'],
                baseline_id=source['baseline_id'],arm_config_sha256=arms),center_confirmed=False)
            for phase in ['above','grasp']:
                identifier=fields.get(phase+'_frame')
                if not identifier:continue
                record=self.teaching.evidence(identifier)
                if record.get('teaching_kind')!=phase or record.get('arm_config_sha256')!=arms or record['captured_at']<=source['captured_at']:
                    raise ValueError('旧姿态照片的阶段、配置或采集先后不匹配')
                slot[phase]=dict(frame_id=identifier,captured_at=record['captured_at'],angles=record['targets']+[None],
                    readings=record['joints'],image_size=record['image_size'],has_image=record.get('has_image',True),arm_config_sha256=arms)
            sample=next((s for s in setup['samples'] if s.get('target_frame')==source['id'] and s.get('receipts')=={
                'above':slot.get('above',{}).get('frame_id'),'grasp':slot.get('grasp',{}).get('frame_id')}),None)
            if sample:slot.update(pixel=sample['pixel'],center_confirmed=True)
            old=cfg['slots'][index].get('target')
            if old and old['frame_id']!=source['id']:self.invalidate_sample(old)
            cfg['slots'][index]=slot;self.save(cfg,fields['expected_cards_sha256'],'import_teaching_card_photos')
            return self.status()

    def checked_slot(self, fields):
        slot = fields.get('slot')
        if type(slot) is not int or not 0 <= slot < 10:raise ValueError('卡片编号须为0～9')
        if fields.get('expected_cards_sha256') != self.sha():raise ValueError('卡片已被其他页面更新，请刷新')
        if fields.get('expected_sha256') != self.teaching.sha():raise ValueError('示教配置已变化，请刷新')
        return slot

    @contextmanager
    def transaction(self):
        lock = self.path.with_suffix('.lock')
        try:descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:raise ValueError('卡片正在保存，请刷新后再操作')
        os.close(descriptor)
        try:yield
        finally:lock.unlink()

    def save(self, cfg, expected, operation):
        self.control.audit(dict(operation=operation, cards=cfg, moves=0))
        if self.sha() != expected:raise ValueError('卡片并发修改，未覆盖')
        if self.path.exists():
            self.path.with_name('teaching_cards.before_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json').write_bytes(self.raw())
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(cfg,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        if self.sha() != expected:raise ValueError('卡片在保存期间变化，未覆盖')
        temporary.replace(self.path)
        if self.path not in self.control.extra_audit_paths:self.control.extra_audit_paths.append(self.path)
        self.control.pending = None

    def record(self, fields):
        with self.control.lock, self.transaction():
            self.control.idle();index = self.checked_slot(fields);phase = fields.get('phase')
            if phase not in (['reference'] if index == 0 else ['target','above','grasp']):raise ValueError('阶段与卡片不匹配')
            cfg = self.load();slot = cfg['slots'][index]
            if phase in ['above','grasp']:
                source = slot.get('target')
                if not source or self.status()['slots'][index]['stale']:raise ValueError('先在观察姿态拍本卡片的木块位置')
            angles = None
            if phase != 'target':
                values = fields.get('angles')
                if not isinstance(values,list) or len(values) != 6:raise ValueError('请填写六关节明确目标')
                angles = [arm.angle(self.control.config(),i,v) for i,v in enumerate(values,1)]
            expected = self.sha()
            request=dict(kind=phase,angles=angles[:5] if angles else None,expected_sha256=fields['expected_sha256'])
            if angles:request['gripper_target']=angles[5]
            result = self.teaching.capture_joint_receipt(request) if phase=='grasp' else self.teaching.capture(request)
            record = dict(frame_id=result['id'], captured_at=result['captured_at'], angles=angles,
                          readings=result['joints'], image_size=result['image_size'],has_image=result.get('has_image',True), arm_config_sha256=digest(self.control.config()))
            if phase == 'target':
                record.update(pixel=result['target']['pixel'],baseline_id=self.teaching.load()['baseline']['frame_id'])
                self.invalidate_sample(slot.get('target'))
                cfg['slots'][index] = dict(target=record,center_confirmed=False,plans=slot.get('plans',{}))
            else:
                if index and record['captured_at'] <= slot['target']['captured_at']:raise ValueError('姿态照片须晚于木块位置照片')
                if index:self.invalidate_sample(slot.get('target'))
                slot[phase] = record
            self.save(cfg,expected,'record_teaching_card')
            return dict(status=self.status(), teaching=self.teaching.status(), audit=result['audit'], phase=phase, slot=index)

    def invalidate_sample(self, target):
        if not target:return
        matches=[s['id'] for s in self.teaching.load()['samples'] if s.get('target_frame')==target['frame_id']]
        for identifier in matches:
            self.teaching.samples(dict(action='delete',sample_id=identifier,expected_sha256=self.teaching.sha()))

    def angles(self, fields):
        with self.control.lock, self.transaction():
            self.control.idle();index=self.checked_slot(fields);phase=fields.get('phase')
            if phase not in (['reference'] if index==0 else ['above','grasp']):raise ValueError('角度阶段与卡片不匹配')
            cfg=self.load();slot=cfg['slots'][index];previous=slot.get('plans',{}).get(phase)
            state_status=self.control.states.status()
            identifier=previous.get('id') if previous else None
            if identifier and not any(s['id']==identifier for s in state_status['states']):identifier=None
            record=dict(name=LABELS[index]+'·'+{'reference':'观察','above':'取物上方','grasp':'抓取'}[phase],
                angles=fields.get('angles'),order=fields.get('order'),motion_ms=fields.get('motion_ms'),teaching_stage=phase)
            analysis=self.control.analyze_state(dict(**record,id=identifier,expected_sha256=state_status['sha256']))
            saved=self.control.save_state(dict(token=analysis['token']))
            state=next(s for s in saved['status']['states'] if s['name']==record['name'])
            if (previous and previous['angles']!=state['angles']) or (slot.get(phase) and slot[phase].get('angles')!=state['angles']):
                slot.pop(phase,None);self.invalidate_sample(slot.get('target'))
            elif previous and (previous['order']!=state['order'] or previous['motion_ms']!=state['motion_ms']):
                self.invalidate_sample(slot.get('target'))
            slot.setdefault('plans',{})[phase]=state
            self.save(cfg,fields['expected_cards_sha256'],'save_teaching_card_angles')
            return dict(status=self.status(),control=self.control.status(),teaching=self.teaching.status())

    def confirm(self, fields):
        with self.control.lock, self.transaction():
            self.control.idle();index = self.checked_slot(fields)
            if index == 0:raise ValueError('空桌面不确认木块中心')
            cfg = self.load();slot = cfg['slots'][index];target = slot.get('target')
            if not target:raise ValueError('先拍木块照片')
            point = fields.get('pixel')
            if not isinstance(point,list) or len(point)!=2 or any(type(x) not in (int,float) or not np.isfinite(x) for x in point):
                raise ValueError('木块中心坐标无效')
            if fields.get('confirmed') is not True or np.linalg.norm(np.asarray(point)-target['pixel']) > 2:
                raise ValueError('请明确核对检测中心；偏差超过2像素须重新拍照')
            slot['pixel']=point;slot['center_confirmed']=True
            self.save(cfg,self.sha(),'confirm_teaching_card_center')
            return self.status()

    def preview(self, fields):
        with self.control.lock:
            self.control.idle();index=self.checked_slot(fields);slot=self.status()['slots'][index]
            if not slot['complete']:raise ValueError('先拍木块、确认中心，再记录上方和抓取姿态')
            grip=self.control.config().get('gripper',{})
            for phase,kind in [('above','open'),('grasp','closed')]:
                value=slot[phase]['angles'][5]
                if value is not None and value!=grip.get(kind):
                    raise ValueError('卡片6号目标与公共夹爪开合参数不同，请先核对：上方用张开目标，抓取用夹持目标')
            return self.teaching.sample_draft(dict(expected_sha256=fields['expected_sha256'],center_confirmed=True,
                pixel=slot['pixel'],target_frame=slot['target']['frame_id'],above_frame=slot['above']['frame_id'],grasp_frame=slot['grasp']['frame_id'],
                stage_orders={phase:slot.get('plans',{}).get(phase,{}).get('order') for phase in ['above','grasp']},
                stage_motion_ms={phase:slot.get('plans',{}).get(phase,{}).get('motion_ms') for phase in ['above','grasp']}))
