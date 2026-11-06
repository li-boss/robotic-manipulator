"""只读核对旧“九点示教”资料，供抓放规划作逆解引导参考。

旧版拍照示教 / pickup_only 页面已删除，这里只保留“读取并核对已有资料”的部分：
teaching_setup.json（九点映射）、teaching_cards.json（十张卡片）、logs/calibration/
里的原照片与元数据，以及 manual_states.json 中卡片引用的姿态按钮。
本模块不采样、不读写舵机，也不修改任何资料。资料不存在时规划自动退回纯几何逆解。
"""
import hashlib
import json
import re

import cv2
import numpy as np

import arm
from saved_states import checked as checked_state

LABELS = ['空桌面观察', '左上', '上中', '右上', '左中', '中心', '右中', '左下', '下中', '右下']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _pose(arms, values):
    if not isinstance(values, list) or len(values) != 5 or any(not arm.number(x) for x in values):
        raise ValueError('明确关节目标需要5个有限数值')
    return [arm.angle(arms, i, value) for i, value in enumerate(values, 1)]


class NinePointSource:
    def __init__(self, control, data_dir):
        self.control = control
        self.path = data_dir / 'teaching_setup.json'
        self.cards_path = data_dir / 'teaching_cards.json'
        self.samples = data_dir / 'logs/calibration'
        self.teaching_logs = data_dir / 'logs/teaching'
        for path in (self.path, self.cards_path):
            if path.exists():
                control.extra_audit_paths.append(path)

    # ---- 读取 -------------------------------------------------------------
    def load(self):
        if not self.path.exists():
            return dict(version=1, baseline=None, common={}, samples=[], validations=[], arm_config_sha256=None)
        return json.loads(self.path.read_text(encoding='utf-8-sig'))

    def cards(self):
        raw = self.cards_path.read_bytes() if self.cards_path.exists() else b''
        cfg = json.loads(raw.decode('utf-8-sig')) if raw else dict(version=1, slots=[{} for _ in LABELS])
        if cfg.get('version') != 1 or not isinstance(cfg.get('slots'), list) or len(cfg['slots']) != 10:
            raise ValueError('十卡片数据格式异常')
        return cfg, hashlib.sha256(raw).hexdigest()

    def metadata(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('无效示教画面编号')
        return json.loads((self.samples / (identifier + '.json')).read_text(encoding='utf-8'))

    def evidence(self, identifier):
        meta = self.metadata(identifier)
        if meta.get('real') is not True:
            raise ValueError('九点资料需要真实采样证据，禁止把模拟读数当作实测')
        suffixes = ['.json'] if meta.get('record_type') == 'joint_receipt' else ['.json', '.jpg']
        for suffix in suffixes:
            path = self.samples / (identifier + suffix)
            if path not in self.control.extra_audit_paths:
                self.control.extra_audit_paths.append(path)
        return meta

    # ---- 核对 -------------------------------------------------------------
    def prerequisites(self, setup, arms):
        if setup.get('version') != 1 or setup.get('arm_config_sha256') != digest(arms):
            raise ValueError('机械臂配置未绑定或已改变，九点资料失效')
        if not setup.get('baseline'):
            raise ValueError('九点资料缺少空桌面观察姿态')
        _pose(arms, setup['baseline']['angles'])
        arm.timing(arms)
        for key in ('open', 'closed'):
            arm.angle(arms, 6, arms.get('gripper', {}).get(key))
        if arms['gripper']['open'] == arms['gripper']['closed']:
            raise ValueError('夹爪开合目标不能相同')

    def reported_source(self, setup):
        """由照片、确认中心和姿态按钮重建九点来源，用于和保存的映射逐字段比较。"""
        arms = self.control.config()
        self.prerequisites(setup, arms)
        if setup.get('task_mode') != 'pickup_only':
            raise ValueError('九点卡片映射仅来自抓取保持模式')
        baseline_id = setup['baseline']['frame_id']
        self.evidence(baseline_id)
        states = self.control.states.status()
        by_id = {s['id']: s for s in states['states']}
        cards, cards_sha = self.cards()
        samples = []
        for index, slot in enumerate(cards['slots'][1:], 1):
            target = slot.get('target')
            if not target or slot.get('center_confirmed') is not True:
                raise ValueError(LABELS[index] + '缺少已有定位照片或中心确认')
            if target.get('baseline_id') != baseline_id or target.get('arm_config_sha256') != digest(arms):
                raise ValueError(LABELS[index] + '定位资料与当前观察基准或配置不匹配')
            meta = self.evidence(target['frame_id'])
            if (meta.get('teaching_kind') != 'target' or meta.get('baseline_id') != baseline_id
                    or meta.get('arm_config_sha256') != digest(arms)):
                raise ValueError('定位照片元数据不匹配')
            photo = (self.samples / (target['frame_id'] + '.jpg')).read_bytes()
            image = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
            size = setup['baseline']['image_size']
            if image is None or [image.shape[1], image.shape[0]] != size or meta.get('image_size') != size:
                raise ValueError('定位照片缺失、损坏或分辨率改变')
            pixel = slot.get('pixel', target.get('pixel'))
            if (not isinstance(pixel, list) or len(pixel) != 2 or any(not arm.number(v) for v in pixel)
                    or np.linalg.norm(np.asarray(pixel) - meta['target']['pixel']) > 2):
                raise ValueError('已确认中心与原定位照片不一致')
            sample = dict(slot=index, name=LABELS[index], pixel=pixel, target_frame=target['frame_id'],
                          descriptor=meta['target']['descriptor'], photo_sha256=hashlib.sha256(photo).hexdigest(),
                          metadata_sha256=hashlib.sha256((self.samples / (target['frame_id'] + '.json')).read_bytes()).hexdigest(),
                          stage_orders={}, stage_motion_ms={})
            for phase, key in (('above', 'open'), ('grasp', 'closed')):
                saved = slot.get('plans', {}).get(phase)
                if not saved or saved.get('id') not in by_id:
                    raise ValueError(LABELS[index] + '缺少明确的' + phase + '目标按钮')
                plan = checked_state(arms, saved)
                if plan != checked_state(arms, by_id[saved['id']]):
                    raise ValueError('卡片目标与已保存姿态不同')
                if plan['teaching_stage'] != phase or plan['angles'][5] != arms['gripper'][key]:
                    raise ValueError('阶段或夹爪目标不匹配')
                if phase == 'grasp' and plan['order'][-1] != 6:
                    raise ValueError('抓取顺序必须最后执行6号夹爪')
                sample[phase] = plan['angles'][:5]
                sample['stage_orders'][phase] = plan['order']
                sample['stage_motion_ms'][phase] = plan['motion_ms']
            samples.append(sample)
        return dict(source_type='user_report', operator_report=True,
                    report='用户报告九个固定位置已经实际验证抓取；新位置为插值试抓',
                    cards_sha256=cards_sha, states_sha256=states['sha256'], samples=samples)

    def bound(self, setup):
        """九点资料与当前配置、照片、姿态按钮全部一致才返回；任一变化即报错（规划会拒绝而非悄悄忽略）。"""
        if setup.get('arm_config_sha256') != digest(self.control.config()):
            raise ValueError('机械臂配置已变化，九点资料失效')
        for record in [setup['baseline']] + list(setup.get('common', {}).values()):
            if record:
                self.evidence(record['frame_id'])
        for record in setup.get('samples', []) + setup.get('validations', []):
            proof = record.get('evidence', {})
            filename = proof.get('file')
            if (not isinstance(filename, str) or filename != record['id'] + '.json'
                    or not all(c in '0123456789abcdef' for c in record['id'])):
                raise ValueError('验证证据编号无效')
            path = self.teaching_logs / filename
            if hashlib.sha256(path.read_bytes()).hexdigest() != proof.get('sha256'):
                raise ValueError('真实验证证据缺失或已改变')
            if path not in self.control.extra_audit_paths:
                self.control.extra_audit_paths.append(path)
        if setup.get('mapping_mode') == 'reported_cards':
            source = self.reported_source(setup)
            saved = {k: v for k, v in setup.get('reported_mapping', {}).items() if k != 'fit'}
            if source != saved:
                raise ValueError('九点照片、中心、目标或顺序已改变，九点资料失效')
