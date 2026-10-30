"""用户明确填写的六关节姿态；存储和分析不连接硬件。"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

from core import arm

STAGES = ['', 'reference', 'target', 'transfer', 'place_above', 'place', 'above', 'grasp']


def checked(config, fields):
    name = fields.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60 or any(ord(c) < 32 for c in name):
        raise ValueError('姿态名称须为1～60个可见字符')
    values, order = fields.get('angles'), fields.get('order')
    if not isinstance(values, list) or len(values) != 6:
        raise ValueError('请填写六个明确目标，按1～6号排列')
    values = [arm.angle(config, i, value) for i, value in enumerate(values, 1)]
    if not isinstance(order, list) or len(order) != 6 or any(type(i) is not int for i in order) or set(order) != set(range(1, 7)):
        raise ValueError('执行顺序须包含1～6号各一次，例如6,1,2,3,4,5')
    duration, _ = arm.timing(dict(config, motion_ms=fields.get('motion_ms')))
    stage = fields.get('teaching_stage', '')
    if stage not in STAGES:
        raise ValueError('未知标定阶段')
    return dict(name=name.strip(), angles=values, order=order.copy(), motion_ms=duration, teaching_stage=stage)


class StateStore:
    def __init__(self, path):
        self.path = Path(path)

    def raw(self):
        return self.path.read_bytes() if self.path.exists() else b''

    def status(self):
        raw = self.raw()
        data = json.loads(raw.decode('utf-8-sig')) if raw else dict(version=1, states=[])
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('states'), list):
            raise ValueError('姿态按钮文件格式异常')
        return dict(sha256=hashlib.sha256(raw).hexdigest(), states=data['states'])

    def save(self, record, expected, identifier=None, delete=False):
        lock = self.path.with_suffix('.lock')
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError('姿态按钮文件正在保存，请稍后重新分析')
        os.close(descriptor)
        try:
            status = self.status()
            if status['sha256'] != expected:
                raise ValueError('姿态按钮已被其他页面修改，请刷新并重新分析')
            states = status['states']
            existing = next((i for i, item in enumerate(states) if item['id'] == identifier), None)
            if identifier is not None and existing is None:
                raise ValueError('要修改的姿态按钮不存在')
            if delete:
                if existing is None:
                    raise ValueError('要删除的姿态按钮不存在')
                states.pop(existing)
            else:
                if any(item['name'] == record['name'] and item['id'] != identifier for item in states):
                    raise ValueError('同名姿态已经存在，请使用编辑按钮')
                record = dict(record, id=identifier or secrets.token_hex(16), updated_at=arm.now(), path_verified=False)
                if existing is None:
                    states.append(record)
                else:
                    states[existing] = record
            backup = None
            if self.path.exists():
                backup = self.path.with_name(self.path.name + '.before_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
                shutil.copy2(self.path, backup)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(version=1, states=states), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            if hashlib.sha256(self.raw()).hexdigest() != expected:
                raise ValueError('文件在保存期间变化，未覆盖')
            temporary.replace(self.path)
            return dict(status=self.status(), backup=str(backup) if backup else None)
        finally:
            lock.unlink()
