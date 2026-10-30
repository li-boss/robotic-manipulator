"""无纸模型模式的纯计算：网格包络、腕部相机位姿和参数来源。无硬件访问。"""
import copy
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np

from auto_geometry import MODEL, array, rotation, transform

ASSETS = Path(__file__).parent / 'models/manipulation'


def profile():
    data = (ASSETS / 'cad_profile.json').read_bytes()
    result = json.loads(data)
    if result['urdf_sha256'] != hashlib.sha256(MODEL.read_bytes()).hexdigest():
        raise ValueError('CAD参数与课程模型版本不一致')
    return result


def measured_table_z(source):
    """User-measured height to the model's broad round plate at Z=80 mm."""
    p = profile()
    if (source.get('kind') != 'measured_model_datum' or
            source.get('reference_surface') != 'base_round_plate_top_v1' or
            type(source.get('reference_z_mm')) not in (int, float) or source['reference_z_mm'] != 80 or
            source.get('urdf_sha256') != p['urdf_sha256'] or
            source.get('mesh_sha256') != p['mesh_sha256']['base_link.STL']):
        raise ValueError('桌面实测基准与当前模型不符')
    height = source.get('measured_height_mm')
    if type(height) not in (int, float) or not math.isfinite(height) or not 0 <= height <= 1000:
        raise ValueError('桌面至基准面的实测高度须为0～1000毫米有限数值')
    if source.get('user_confirmed') is not True or not isinstance(source.get('work_surface'), str) or not source['work_surface'].strip():
        raise ValueError('桌面实测来源缺少工作平面和用户确认')
    return 80. - height


def link_matrices(cfg, angles):
    q = array(angles, (5,), '相机位姿的真实关节读数')
    result = [np.eye(4)]
    xml = ET.parse(MODEL)
    zero = array(cfg['model']['servo_zero_deg'], (5,), '模型零点')
    signs = array(cfg['model']['servo_sign'], (5,), '模型方向')
    if not np.all(np.isin(signs, [-1, 1])):
        raise ValueError('模型方向必须为+1或-1')
    for i in range(5):
        joint = xml.find("joint[@name='joint%d']" % (i + 1))
        origin = joint.find('origin')
        xyz = np.array([float(v) for v in origin.get('xyz').split()])
        rpy = [float(v) for v in origin.get('rpy').split()]
        axis = [float(v) for v in joint.find('axis').get('xyz').split()]
        motion = np.eye(4)
        motion[:3, :3] = rotation(axis, math.radians((q[i] - zero[i]) * signs[i]))
        result.append(result[-1] @ transform(xyz, rpy) @ motion)
    return result


def camera_extrinsics(cfg, angles):
    mount = cfg['cad']['camera_mount']
    local = np.eye(4)
    local[:3, :3] = array(mount['rotation_optical_to_link'], (3, 3), '相机模型旋转')
    local[:3, 3] = array(mount['xyz_mm'], (3,), '相机模型位置') / 1000
    if not np.allclose(local[:3, :3].T @ local[:3, :3], np.eye(3), atol=1e-8) or abs(np.linalg.det(local[:3, :3]) - 1) > 1e-8:
        raise ValueError('相机旋转必须正交且保持右手坐标系')
    # 相机属于link4，而不是随5号夹爪转动的link5。
    world = link_matrices(cfg, angles)[4] @ local
    camera = np.linalg.inv(world)
    return dict(rvec=cv2.Rodrigues(camera[:3, :3])[0].reshape(3).tolist(),
                tvec_mm=(camera[:3, 3] * 1000).tolist())


def intrinsics(size, value):
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        raise ValueError('相机分辨率无效')
    if not isinstance(value, dict):
        raise ValueError('需要相机参数来源')
    if value.get('kind') == 'imported':
        if value.get('image_size') != size:
            raise ValueError('导入内参分辨率须与冻结原图完全相同，禁止静默缩放或裁切')
        k = array(value.get('matrix'), (3, 3), '导入内参')
        d = array(value.get('distortion'), (5,), '导入畸变')
        if not isinstance(value.get('source'), str) or not value['source'].strip():
            raise ValueError('导入参数需要来源说明')
        source = dict(kind='imported', description=value['source'], accuracy_verified=False)
    elif value.get('kind') == 'fov_estimate':
        degrees = value.get('fov_deg')
        if type(degrees) not in (int, float) or not math.isfinite(degrees) or not 10 <= degrees <= 160:
            raise ValueError('视场角估计须为10～160度')
        axis = value.get('axis')
        if axis not in ('horizontal', 'diagonal'):
            raise ValueError('须明确视场角按水平还是对角解释')
        span = size[0] if axis == 'horizontal' else math.hypot(*size)
        focal = span / (2 * math.tan(math.radians(degrees) / 2))
        k = np.array([[focal, 0, size[0] / 2], [0, focal, size[1] / 2], [0, 0, 1.]])
        d = np.zeros(5)
        source = dict(kind='fov_estimate', fov_deg=degrees, axis=axis,
                      description='标称视场角估算；假设方形像素、主点在中心、畸变为零', accuracy_verified=False)
    else:
        raise ValueError('未知相机参数来源')
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]) or abs(k[0, 1]) > 1e-8 or abs(k[1, 0]) > 1e-8:
        raise ValueError('内参格式无效')
    if not 0 <= k[0, 2] < size[0] or not 0 <= k[1, 2] < size[1]:
        raise ValueError('主点不在原图范围')
    return k.tolist(), d.tolist(), source


def dynamic_capture(cfg):
    return isinstance(cfg.get('cad'),dict) and cfg['cad'].get('capture_mode')=='joint_snapshot_v1'


def effective_arms(cfg, arms):
    result = copy.deepcopy(arms)
    if cfg.get('cad') is not None:
        result['gripper'] = copy.deepcopy(cfg['cad']['gripper'])
    return result


def obb_overlap(a, b, margin=0):
    """分离轴检查两个旋转盒；零轴跳过，接触视为相交。长度均为毫米。"""
    ca, ra, ha = a
    cb, rb, hb = b
    delta = cb - ca
    # 宽阶段先排除，减少连续扫掠时SAT调用。
    if np.any(abs(delta) > abs(ra) @ ha + abs(rb) @ hb + 2 * margin):
        return False
    # Same 15 separating axes and margins, evaluated in one NumPy batch.
    # Continuous sweep samples and collision geometry are unchanged.
    axes = np.vstack((ra.T, rb.T, np.cross(ra.T[:,None,:],rb.T[None,:,:]).reshape(-1,3)))
    lengths = np.linalg.norm(axes,axis=1)
    axes = axes[lengths>=1e-9] / lengths[lengths>=1e-9,None]
    radius = abs(axes@ra)@ha + abs(axes@rb)@hb + 2*margin
    return not np.any(abs(axes@delta)>radius)


def collision(robot, values, carried=None, margin=0):
    """CAD模式以分解的网格盒覆盖模型，包含相机和夹爪全开候选包络。"""
    cfg = robot.cfg
    m, _ = robot.fk(values, False)
    matrices = np.asarray(robot.matrices(values))
    links=robot._cad_box_links
    rotations=matrices[links,:3,:3];halves=robot._cad_box_halves
    centers=np.einsum('nij,nj->ni',rotations,robot._cad_box_centers)+matrices[links,:3,3]*1000
    extents=np.einsum('nij,nj->ni',abs(rotations),halves)
    bounds = robot.bounds
    if np.any(centers-extents-margin<bounds[0]) or np.any(centers+extents+margin>bounds[1]):
        raise ValueError('CAD外形包络超出允许运动空间')
    # 对桌面采用整个静态网格凸包的精确支撑点，避免把包围盒的空隙当作实体。
    # base_link/link1为基座安装区；夹爪另计最大开口候选盒的八个顶点。
    for i in range(2,6):
        pose=matrices[i];points=robot._cad_floor_support[i]
        low=float(np.min(np.sum(points*pose[2,:3],axis=1))+pose[2,3]*1000)
        if i==5:
            box=cfg['cad']['collision_boxes'][5][-1];c=np.asarray(box['center_mm']);h=np.asarray(box['half_size_mm'])
            low=min(low,float(pose[2,:3]@c+pose[2,3]*1000-abs(pose[2,:3])@h))
        if low-margin<=cfg['table_z_mm']:
            raise ValueError('CAD连杆、相机或夹爪包络接近桌面')
    reach=robot._cad_reach
    distances=np.linalg.norm(centers[:,None]-np.asarray([p[:3,3]*1000 for p in matrices[1:]])[None,:],axis=2)
    radii=robot._cad_box_radii
    local_margin=np.sum((distances+radii[:,None])*(np.arange(1,6)[None,:]<=links[:,None]),axis=1)*(margin/reach)
    base_pair=(links[:,None]==0)|(links[None,:]==0)
    padding=np.where(base_pair,local_margin[:,None]+local_margin[None,:],2*margin)
    broad=np.all(abs(centers[:,None]-centers[None,:])<=extents[:,None]+extents[None,:]+padding[:,:,None],axis=2)
    pairs=np.triu(broad&(abs(links[:,None]-links[None,:])>1),1)
    for left,right in zip(*np.nonzero(pairs)):
        # 底座固定，使用该盒至各上游关节的距离界，而不是整臂末端位移。
        pair_margin=padding[left,right]/2
        if obb_overlap((centers[left],rotations[left],halves[left]),(centers[right],rotations[right],halves[right]),pair_margin):
            raise ValueError('CAD非相邻连杆、相机或夹爪包络相交')
    if carried is not None:
        pose = m @ carried
        block = (pose[:3, 3] * 1000, pose[:3, :3], np.asarray(cfg['block_size_mm']) / 2)
        extent = abs(block[1]) @ block[2]
        if block[0][2] - extent[2] < cfg['table_z_mm'] - .001:
            raise ValueError('携带方块接触或穿过桌面')
        if np.any(block[0] - extent - margin < bounds[0]) or np.any(block[0] + extent + margin > bounds[1]):
            raise ValueError('携带方块超出允许空间')
        for i, shape in zip(links,zip(centers,rotations,halves)):
            if i < 5 and obb_overlap(block, shape, margin):
                raise ValueError('携带方块接近机械臂或相机')
    else:
        block = None
    for obstacle in cfg['obstacles']:
        other = (array(obstacle['center_mm'], (3,), '障碍中心'), np.eye(3),
                 array(obstacle['half_size_mm'], (3,), '障碍半尺寸'))
        if np.any(other[2] <= 0):
            raise ValueError('障碍尺寸须为正')
        if any(obb_overlap(shape, other, margin) for shape in zip(centers,rotations,halves)) or (block is not None and obb_overlap(block, other, margin)):
            raise ValueError('模型或携带方块接近已登记障碍')
    return m


def validate(cfg):
    cad = cfg['cad']
    if not isinstance(cad,dict):raise ValueError('CAD配置结构无效')
    p = profile()
    if cad.get('profile_sha256') != hashlib.sha256((ASSETS / 'cad_profile.json').read_bytes()).hexdigest():
        raise ValueError('CAD资源版本变化，请重新生成候选配置')
    if cad.get('collision_boxes') != p['collision_boxes']:
        raise ValueError('CAD包络与网格提取记录不同，不能缩小或手工伪造')
    if cad.get('camera_mount', {}).get('link') != 4:
        raise ValueError('本模型相机安装于link4')
    if type(cad.get('assumptions_confirmed')) is not bool:
        raise ValueError('CAD候选采用状态无效')
    for boxes in cad['collision_boxes']:
        if not isinstance(boxes, list) or not boxes:
            raise ValueError('CAD连杆缺少碰撞包络')
        for box in boxes:
            array(box['center_mm'], (3,), 'CAD包络中心')
            if np.any(array(box['half_size_mm'], (3,), 'CAD包络半尺寸') <= 0):
                raise ValueError('CAD包络尺寸须为正')
    k, d, source = intrinsics(cfg['camera']['image_size'], cad['intrinsics_recipe'])
    if cfg['camera']['matrix'] != k or cfg['camera']['distortion'] != d or cad['intrinsics_source'] != source:
        raise ValueError('相机参数与导入/估计来源不符，请重新生成候选')
    if not np.allclose(cfg['model']['tcp_xyz_mm'], p['tcp_xyz_mm']) or cfg['model']['tcp_rpy_deg'] != p['tcp_rpy_deg']:
        raise ValueError('CAD夹持中心与提取记录不符')
    if cad.get('capture_mode','fixed_observation') not in ('fixed_observation','joint_snapshot_v1'):
        raise ValueError('未知相机采样模式')
    camera_extrinsics(cfg, cfg['observation_readings'])


def prepare(cfg, arms, meta, targets, recipe, image_roll_deg=0, capture_mode='fixed_observation'):
    """按已有目标和冻结真实读数建立候选，不自动生成任何运动指令。"""
    from core import arm
    from auto_plan import arm_fingerprint
    p = profile()
    if capture_mode not in ('fixed_observation','joint_snapshot_v1'):raise ValueError('未知相机采样模式')
    dynamic=capture_mode=='joint_snapshot_v1'
    q=None if dynamic else array(targets,(5,),'观察姿态明确目标').tolist()
    if q is not None:q=[arm.angle(arms,i,v) for i,v in enumerate(q,1)]
    rows = meta['joints']
    if len(rows) != 6 or any(r['id'] != i or r['read_ok'] is not True for i, r in enumerate(rows, 1)):
        raise ValueError('需要完整真实读数；不从读数截断生成目标')
    for i, r in enumerate(rows, 1):
        arm.observed_angle(arms, i, r['angle'])
    raw = [r['angle'] for r in rows[:5]]
    if not dynamic and np.max(abs(np.asarray(raw) - q)) > 1:
        raise ValueError('冻结时的姿态与明确观察目标不同，请先在原页面确认并到位')
    if type(image_roll_deg) is not int or image_roll_deg not in (0, 90, 180, 270):
        raise ValueError('图像轴候选旋转只能为0/90/180/270度')
    k, d, source = intrinsics(meta['image_size'], recipe)
    result = copy.deepcopy(cfg)
    block = array(cfg['block_size_mm'], (3,), '方块尺寸')
    if not np.allclose(block, [30, 30, 30]):
        raise ValueError('当前CAD夹爪全行程候选针对用户已报告的30毫米方块')
    measured_table = (cfg.get('cad') or {}).get('camera_calibration', {}).get('table_height_source')
    if measured_table and measured_table.get('kind') == 'measured_model_datum':
        if abs(cfg['table_z_mm']-measured_table_z(measured_table)) > 1e-6:
            raise ValueError('桌面高度与独立实测基准不符')
    # A source-bound hand-eye adoption may explicitly replace the old nominal height.
    # Manipulation.prepare_cad verifies the referenced evidence before using it.
    if cfg.get('table_z_mm') != -16 and not measured_table:
        raise ValueError('请先核对已报告的80毫米转轴高度所对应桌面Z=-16')
    opening = arm.angle(arms, 6, 10)
    closed = arm.angle(arms, 6, arms.get('gripper', {}).get('closed'))
    # 只采用已有的夹持目标，不以完全闭合170度替代带块夹持目标。
    # 按URDF平移范数计算可复核的伸展上界。
    xml = ET.parse(MODEL)
    reach = sum(np.linalg.norm([float(v) * 1000 for v in j.find('origin').get('xyz').split()]) for j in xml.findall('joint'))
    reach += np.linalg.norm(p['tcp_xyz_mm'])
    extent = math.ceil((reach + 100) / 10) * 10
    sweep_margin = reach * math.radians(1.25)
    grasp_height = float(block[2] * .75)
    transfer_height = float(grasp_height + block[2] + 2 * sweep_margin)
    result.update(model_reviewed=False, calibration_reviewed=False, arm_config_sha256=arm_fingerprint(arms),
        observation_angles=q, observation_readings=raw, grasp_reference_angles=None if dynamic else q.copy(),
        jaw_open_width_mm=60, max_position_error_mm=(8. if cfg.get('max_position_error_mm') is None else cfg['max_position_error_mm']), max_direction_error_deg=3.,
        grasp_height_mm=grasp_height, transfer_height_mm=transfer_height, release_gap_mm=2.,
        workspace_xy_mm=[[-extent, -extent], [extent, -extent], [extent, extent], [-extent, extent]],
        motion_bounds_mm=[[-extent, -extent, -extent], [extent, extent, extent]],
        scene_revision='CAD候选：现场空域与路径待核对', checks=[], path_validations=[], destinations=copy.deepcopy(cfg.get('destinations',{})))
    result['model'].update(tcp_xyz_mm=p['tcp_xyz_mm'], tcp_rpy_deg=p['tcp_rpy_deg'], jaw_axis_tcp=p['jaw_axis_tcp'],
        collision_radii_mm=p['capsule_radii_mm'], tool_radius_mm=p['tool_radius_mm'],
        tool_boxes_mm=[dict(center=b['center_mm'], half_size=b['half_size_mm']) for b in p['collision_boxes'][5]],
        base_box_mm=p['base_box_mm'], measurements=[], independent_measurements=[])
    mount = copy.deepcopy(p['camera_mount'])
    mount['rotation_optical_to_link'] = (np.asarray(mount['rotation_optical_to_link']) @ rotation([0, 0, 1], math.radians(image_roll_deg))).tolist()
    result['cad'] = dict(capture_mode=capture_mode,profile_sha256=hashlib.sha256((ASSETS / 'cad_profile.json').read_bytes()).hexdigest(),
        camera_mount=mount, image_roll_deg=image_roll_deg, collision_boxes=p['collision_boxes'],
        intrinsics_source=source, intrinsics_recipe=copy.deepcopy(recipe), anchor_frame=meta['id'],
        gripper=dict(open=opening, closed=closed), assumptions_confirmed=False,
        notes=['机械安装按用户声明与CAD一致；模型零点/方向采用课程约定',
               '夹持中心取指端几何区域中点；光心取镜头前面中心候选',
               '运动空间由模型伸展上界生成，需要现场确认桌面边界、线缆及空域',
               '抓取高度取方块高度3/4，转移高度含方块高度及扫掠余量；均为待核对计划参数',
               '误差阈值是软件筛选标准，不是已测定位精度；没有登记实物标定成功'])
    result['camera'].update(matrix=k, distortion=d, image_size=meta['image_size'], base_to_camera=None,
                            anchor_frame=None, intrinsics_certificate=None, samples=[])
    validate(result)
    return result
