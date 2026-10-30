"""本地从已散列核对的STL生成CAD候选；不连接任何设备。"""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def boxes(faces, depth=3):
    if depth and len(faces) > 100:
        center = faces.mean(axis=1)
        axis = np.ptp(center, axis=0).argmax()
        split = np.median(center[:, axis])
        left = center[:, axis] < split
        if left.any() and not left.all():
            return boxes(faces[left], depth - 1) + boxes(faces[~left], depth - 1)
    lo = np.floor(faces.min(axis=(0, 1)) * 1000) / 1000
    hi = np.ceil(faces.max(axis=(0, 1)) * 1000) / 1000
    return [dict(center_mm=((lo + hi) / 2).tolist(), half_size_mm=np.maximum((hi - lo) / 2, .001).tolist())]


def main():
    from scipy.spatial import ConvexHull  # 仅本地预处理依赖；设备运行不需要SciPy。
    folder = ROOT / 'models/manipulation'
    manifest = json.loads((folder / 'manifest.json').read_text())
    meshes = []
    dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
    for item in manifest['meshes']:
        data = (folder / 'meshes' / item['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('网格版本变化')
        faces = np.frombuffer(data, dtype=dtype, offset=84)['vertices'].astype(float) * 1000
        meshes.append(faces)
    vertices = meshes[4].reshape(-1, 3)
    # 该已核对模型中镜头圆柱：与肩部舵机外形分离，位于link4的-Y侧。
    lens = vertices[(vertices[:, 1] < -40) & (vertices[:, 0] < -65)]
    lo, hi = lens.min(axis=0), lens.max(axis=0)
    if len(lens) < 100 or not 12 < hi[1] - lo[1] < 16 or not 12 < hi[2] - lo[2] < 16:
        raise ValueError('镜头候选几何不符合已审阅形状')
    xyz = [float(lo[0]), float((lo[1] + hi[1]) / 2), float((lo[2] + hi[2]) / 2)]
    fingers = meshes[5][np.min(meshes[5][:, :, 2], axis=1) > 90]
    finger_lo, finger_hi = fingers.min(axis=(0, 1)), fingers.max(axis=(0, 1))
    tcp = ((finger_lo + finger_hi) / 2).tolist()
    colliders = [boxes(f, 2 if i == 0 else (5 if i == 5 else 3)) for i, f in enumerate(meshes)]
    # 覆盖最大60mm开口：两侧各保留完整单指宽度，不把静态半开网格当作全行程。
    width = max(float(np.ptp(fingers.reshape(-1, 3)[fingers.reshape(-1, 3)[:, 0] < 0, 0])),
                float(np.ptp(fingers.reshape(-1, 3)[fingers.reshape(-1, 3)[:, 0] > 0, 0])))
    center = ((finger_lo + finger_hi) / 2).tolist()
    half = ((finger_hi - finger_lo) / 2).tolist()
    half[0] = 30 + width
    colliders[5].append(dict(center_mm=center, half_size_mm=half))
    radii=[]
    for index in range(1,6):
        vertices=meshes[index].reshape(-1,3)
        end=(np.asarray(manifest['joints'][index]['xyz'])*1000 if index<5 else np.asarray(tcp))
        fraction=np.clip(np.sum(vertices*end,axis=1)/np.sum(end*end),0,1)
        radii.append(float(np.ceil(np.linalg.norm(vertices-fraction[:,None]*end,axis=1).max()*1000)/1000))
    base_lo=meshes[0].min(axis=(0,1));base_hi=meshes[0].max(axis=(0,1))
    result = dict(version=1, urdf_sha256=manifest['kinematics_source_sha256'],
        mesh_sha256={m['file']: m['sha256'] for m in manifest['meshes']},
        camera_mount=dict(link=4, xyz_mm=xyz, rotation_optical_to_link=[[0, 0, -1], [0, 1, 0], [1, 0, 0]],
            source='镜头圆柱前表面中心与轴线拟合；光心前后偏移和图像轴采用候选约定，未实测'),
        tcp_xyz_mm=tcp, tcp_rpy_deg=[0, 0, 0], jaw_axis_tcp=[1, 0, 0],
        collision_boxes=colliders,
        capsule_radii_mm=radii,tool_radius_mm=float(np.ceil(np.linalg.norm(meshes[5],axis=2).max()*1000)/1000),
        base_box_mm=dict(center_mm=((base_lo+base_hi)/2).tolist(),half_size_mm=((base_hi-base_lo)/2).tolist()),
        note='CAD候选，不能称为实测标定；相机有效光心、图像方向及开合扫掠仍需现场检查',
        floor_support_vertices_mm=[(lambda points:points[ConvexHull(points).vertices].tolist())(
            np.unique(f.reshape(-1,3),axis=0)) for f in meshes],
        accuracy_verified=False)
    path = folder / 'cad_profile.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps(dict(path=str(path), camera_mount=result['camera_mount'], tcp_xyz_mm=tcp,
                          boxes=sum(map(len, colliders)), hardware_reads=0, motor_commands=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
