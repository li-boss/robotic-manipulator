"""仅本地提取模型尺寸和包络候选；不导入控制层、不连接相机或设备。

读取原始STL、课程URDF与用户报告尺寸。只写尺寸报告，不修改运行配置、
标定证据、关节参数或核对状态。模型包络仅覆盖文件中的静态外形。
"""
import hashlib
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parent


def derive():
    assets = ROOT / 'models/manipulation'
    manifest = json.loads((assets / 'manifest.json').read_text())
    urdf = (ROOT / 'models/course/dofbot.urdf').read_bytes()
    if hashlib.sha256(urdf).hexdigest() != manifest['kinematics_source_sha256']:
        raise ValueError('课程URDF与资源清单版本不一致')
    xml = ET.fromstring(urdf)
    joints = []
    for i in range(1, 6):
        joint = xml.find("joint[@name='joint%d']" % i)
        xyz = [float(v) * 1000 for v in joint.find('origin').get('xyz').split()]
        joints.append(dict(name=joint.get('name'), parent=joint.find('parent').get('link'),
                           child=joint.find('child').get('link'), xyz_mm=xyz,
                           origin_distance_mm=math.sqrt(sum(v * v for v in xyz))))
    facts = json.loads((ROOT / '现场已知尺寸.json').read_text())
    table = joints[0]['xyz_mm'][2] - facts['joint1_center_height_above_table_mm']
    meshes = []
    for item in manifest['meshes']:
        data = (assets / 'meshes' / item['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('STL版本不一致：' + item['file'])
        count = struct.unpack_from('<I', data, 80)[0]
        if len(data) != 84 + count * 50:
            raise ValueError('STL长度不一致')
        dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
        vertices = np.frombuffer(data, dtype=dtype, offset=84, count=count)['vertices'].reshape(-1, 3).astype(float)
        vertices *= manifest['native_scale_to_metres'] * 1000
        lo, hi = vertices.min(axis=0), vertices.max(axis=0)
        # 向外取到0.001毫米，避免输出舍入导致包络略小于原始网格。
        lo, hi = np.floor(lo * 1000) / 1000, np.ceil(hi * 1000) / 1000
        mesh = dict(link=item['link'], sha256=item['sha256'],
                    min_mm=lo.tolist(), max_mm=hi.tolist(), size_mm=(hi - lo).tolist(),
                    box=dict(center_mm=((lo + hi) / 2).tolist(), half_size_mm=((hi - lo) / 2).tolist()))
        if item['link'] in ('link1', 'link2', 'link3', 'link4'):
            next_origin = np.asarray(joints[int(item['link'][-1])]['xyz_mm'])
            fraction = np.clip(np.sum(vertices * next_origin, axis=1) / np.sum(next_origin * next_origin), 0, 1)
            radius = np.linalg.norm(vertices - fraction[:, None] * next_origin, axis=1).max()
            mesh['static_capsule_candidate'] = dict(start_mm=[0, 0, 0], end_mm=next_origin.tolist(),
                radius_mm=math.ceil(float(radius) * 1000) / 1000)
        meshes.append(mesh)
    return dict(version=1, units='mm', urdf_sha256=hashlib.sha256(urdf).hexdigest(),
        user_reported=facts, joints=joints, meshes=meshes,
        derived=dict(table_z_mm=table, block_top_z_mm=table + facts['block_size_mm'][2],
                     square_top_diagonal_mm=math.hypot(*facts['block_size_mm'][:2]),
                     ideal_aligned_opening_margin_each_side_mm=(facts['gripper_mechanical_opening_mm']['max'] - facts['block_size_mm'][0]) / 2),
        model_alignment_verified=False, collision_envelopes_verified=False,
        scope='候选包络包含所读静态网格；未证明覆盖真实安装附件、线缆或夹爪全行程；不写入运动配置，不作为实测证据')


def main():
    report = derive()
    path = ROOT / 'models/manipulation/dimensions.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(dict(report=str(path), derived=report['derived'], hardware_reads=0, motor_commands=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
