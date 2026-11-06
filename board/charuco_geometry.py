"""ChArUco fitting and diagnostics. Pure image/matrix operations; no hardware."""
import hashlib
import json
import math

import cv2
import numpy as np


BOARD = dict(id='MA-CHARUCO-9X6-20-14-V1', squares_x=9, squares_y=6,
             square_length_mm=20., marker_length_mm=14., dictionary='DICT_4X4_100',
             legacy_pattern=False, width_mm=180., height_mm=120.)
POINTS = np.array([[20.*x, 20.*y, 0.] for y in range(1, 6) for x in range(1, 9)], np.float32)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def array(value, shape, name):
    a = np.asarray(value)
    if a.shape != shape or a.dtype.kind not in 'iuf' or not np.all(np.isfinite(a)):
        raise ValueError(name+'须为有限数值，尺寸为'+str(shape))
    return a.astype(float)


def make_board():
    if not hasattr(cv2, 'aruco') or not hasattr(cv2.aruco, 'CharucoBoard'):
        raise ValueError('需要独立的OpenCV ArUco识别组件；未读取或移动机械臂')
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
    board = cv2.aruco.CharucoBoard((9, 6), 20., 14., dictionary)
    if not hasattr(board, 'setLegacyPattern'):
        raise ValueError('识别组件过旧，不能保证与非legacy打印板一致')
    board.setLegacyPattern(False)
    if not np.array_equal(board.getChessboardCorners(), POINTS):
        raise ValueError('识别组件的标定板角点定义与已打印版本不同')
    return board


def detect(image):
    board = make_board()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, _, markers = cv2.aruco.CharucoDetector(board).detectBoard(gray)
    points = [] if corners is None else corners.reshape(-1, 2).tolist()
    ids = [] if ids is None else ids.ravel().tolist()
    markers = [] if markers is None else markers.ravel().tolist()
    if len(set(markers)) != len(markers):
        raise ValueError('同一编码出现多次，请只保留一张标定板')
    valid = [(i, p) for i, p in zip(ids, points) if 0 <= i < 40]
    ids, points = [i for i, _ in valid], [p for _, p in valid]
    reasons = []
    if len(ids) < 12:
        reasons.append('至少清晰识别12个内部角点；请靠近、停稳并保留白边')
    coverage = 0.
    sharpness = 0.
    if len(points) >= 4:
        p = np.array(points, np.float32)
        coverage = float(cv2.contourArea(cv2.convexHull(p))/(gray.shape[0]*gray.shape[1]))
        lo = np.maximum(np.floor(p.min(axis=0)-6).astype(int), 0)
        hi = np.minimum(np.ceil(p.max(axis=0)+6).astype(int), [gray.shape[1], gray.shape[0]])
        roi = gray[lo[1]:hi[1], lo[0]:hi[0]]
        sharpness = float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size else 0.
        if coverage < .025:
            reasons.append('标定板在画面中过小，请靠近相机')
        if sharpness < 30:
            reasons.append('标定板画面可能模糊，请停稳、对焦并改善照明')
        world = POINTS[ids, :2]
        if np.ptp(world[:, 0]) < 60 or np.ptp(world[:, 1]) < 40:
            reasons.append('角点分布太集中，请让更多棋盘进入画面')
    return dict(board_id=BOARD['id'], ids=ids, pixels=points,
                marker_ids=[i for i in markers if 0 <= i < 27],
                corner_count=len(ids), marker_count=sum(0 <= i < 27 for i in markers),
                coverage_fraction=coverage, sharpness=sharpness,
                acceptable=not reasons, reasons=reasons, opencv_version=cv2.__version__)


def observation(row):
    ids = row['detection']['ids']
    if not isinstance(ids, list) or len(ids) < 12 or any(type(i) is not int or not 0 <= i < 40 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('角点编号不完整或重复')
    px = array(row['detection']['pixels'], (len(ids), 2), '原图角点').astype(np.float32)
    w, h = row['image_size']
    if np.any(px < 0) or np.any(px >= [w, h]):
        raise ValueError('角点超出原图')
    return POINTS[ids].copy(), px


def transform(r, t):
    result = np.eye(4)
    result[:3, :3] = r
    result[:3, 3] = np.asarray(t).reshape(3)
    return result


def rotation_error(a, b):
    return math.degrees(math.acos(float(np.clip((np.trace(a.T@b)-1)/2, -1, 1))))


def mean_pose(poses):
    u, _, vt = np.linalg.svd(sum(p[:3, :3] for p in poses))
    r = u@np.diag([1, 1, np.linalg.det(u@vt)])@vt
    return transform(r, np.mean([p[:3, 3] for p in poses], axis=0))


def camera_pose(row, k, d):
    obj, px = observation(row)
    ok, rv, t = cv2.solvePnP(obj, px, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError('标定板位姿求解失败')
    r = cv2.Rodrigues(rv)[0]
    if np.any((r@obj.T+t).T[:, 2] <= 0):
        raise ValueError('标定板位于相机后方，位姿无效')
    predicted = cv2.projectPoints(obj, rv, t, k, d)[0].reshape(-1, 2)
    error = float(np.sqrt(np.mean(np.sum((predicted-px)**2, axis=1))))
    return transform(r, t), error


def split(rows, minimum):
    if not rows or len({r['image_size'][0] for r in rows}) != 1 or len({r['image_size'][1] for r in rows}) != 1:
        raise ValueError('所有样本须使用同一原始分辨率')
    if len({r['jpeg_sha256'] for r in rows}) != len(rows):
        raise ValueError('重复原图不能作为不同样本或独立检查')
    fit, check = [r for r in rows if r['role'] == 'fit'], [r for r in rows if r['role'] == 'check']
    if not minimum <= len(fit) <= 40:
        raise ValueError('拟合组需要'+str(minimum)+'～40幅不同样本')
    if len(check) < 3:
        raise ValueError('请另采至少3幅独立检查样本；它们不参与拟合')
    return fit, check


def fit_intrinsics(rows):
    fit, check = split(rows, 10)
    size = tuple(fit[0]['image_size'])
    observations = [observation(r) for r in fit]
    initial = np.array([[size[0], 0, size[0]/2], [0, size[0], size[1]/2], [0, 0, 1.]], float)
    rms, k, d, rv, tv = cv2.calibrateCamera([o for o, _ in observations], [p for _, p in observations],
        size, initial, None, flags=cv2.CALIB_USE_INTRINSIC_GUESS)
    if not np.all(np.isfinite(k)) or not np.all(np.isfinite(d)) or d.size != 5 or k[0, 0] <= 0 or k[1, 1] <= 0 or not (0 <= k[0, 2] < size[0] and 0 <= k[1, 2] < size[1]):
        raise ValueError('内参求解数值无效，请补采分散视角')
    normals = np.array([cv2.Rodrigues(r)[0][:, 2] for r in rv])
    all_pixels = np.vstack([p for _, p in observations])
    span = np.ptp(all_pixels, axis=0)/size
    reasons = []
    if np.linalg.matrix_rank(normals-normals.mean(axis=0), tol=.03) < 2:
        reasons.append('倾斜方向不足，请同时采左右倾斜和前后倾斜')
    if np.any(span < .5):
        reasons.append('样本覆盖不足，请补采画面上下左右位置')
    errors = []
    for row in fit+check:
        _, error = camera_pose(row, k, d)
        errors.append(dict(frame_id=row['id'], role=row['role'], rms_px=error))
    if rms > 1 or any(e['rms_px'] > 1.5 for e in errors):
        reasons.append('拟合RMS须不超过1像素，逐幅拟合与独立检查须不超过1.5像素')
    return dict(kind='intrinsics', image_size=list(size), matrix=k.tolist(), distortion=d.reshape(5).tolist(),
                rms_px=float(rms), per_view=errors, pixel_span_fraction=span.tolist(),
                passed=not reasons, reasons=reasons, accuracy_verified=False,
                message='实拍多视角拟合与留出图像检查；不等于毫米定位或抓取已验收')


def fit_hand_eye(rows, optics):
    fit, check = split(rows, 8)
    k = array(optics['matrix'], (3, 3), '内参')
    d = array(optics['distortion'], (5,), '畸变')
    if any(r['image_size'] != optics['image_size'] for r in rows):
        raise ValueError('手眼样本分辨率与采用的内参不符')
    robot = [array(r['base_to_link4_mm'], (4, 4), 'link4位姿') for r in fit]
    axes = [cv2.Rodrigues(robot[0][:3, :3].T@p[:3, :3])[0].reshape(3) for p in robot[1:]]
    if np.linalg.matrix_rank(np.array(axes), tol=.08) < 2:
        raise ValueError('机械臂姿态变化不足，需要至少两个不平行的旋转方向；只转5号夹爪无效')
    poses, reprojection = [], []
    for row in fit+check:
        pose, error = camera_pose(row, k, d)
        poses.append(pose)
        reprojection.append(dict(frame_id=row['id'], role=row['role'], rms_px=error))
    if any(e['rms_px'] > 1.5 for e in reprojection):
        raise ValueError('手眼样本的标定板位姿重投影误差超过1.5像素')
    r, t = cv2.calibrateHandEye([p[:3, :3] for p in robot], [p[:3, 3] for p in robot],
        [p[:3, :3] for p in poses[:len(fit)]], [p[:3, 3] for p in poses[:len(fit)]], method=cv2.CALIB_HAND_EYE_PARK)
    mount = transform(r, t)
    if not np.all(np.isfinite(mount)) or not np.allclose(r.T@r, np.eye(3), atol=1e-6) or abs(np.linalg.det(r)-1) > 1e-6:
        raise ValueError('手眼求解退化，请采集更多不同姿态')
    base_boards = [array(row['base_to_link4_mm'], (4, 4), 'link4位姿')@mount@p for row, p in zip(fit+check, poses)]
    reference = mean_pose(base_boards[:len(fit)])
    errors = []
    for row, p in zip(fit+check, base_boards):
        errors.append(dict(frame_id=row['id'], role=row['role'],
            translation_mm=float(np.linalg.norm(p[:3, 3]-reference[:3, 3])),
            rotation_deg=rotation_error(reference[:3, :3], p[:3, :3])))
    reasons = []
    limits = dict(translation_mm=10, rotation_deg=2)
    if any(e['translation_mm'] > limits['translation_mm'] or e['rotation_deg'] > limits['rotation_deg'] for e in errors):
        reasons.append('固定板一致性未通过：所有拟合与独立检查须在10毫米、2度内；请检查板是否移动、关节模型和读数')
    return dict(kind='hand_eye', camera_mount=dict(link=4, xyz_mm=mount[:3, 3].tolist(),
        rotation_optical_to_link=mount[:3, :3].tolist(), source='ChArUco多姿态手眼实拍拟合'),
        link4_to_camera_mm=mount.tolist(), base_to_board_mm=reference.tolist(), per_view=errors,
        reprojection=reprojection, consistency_limits=limits, passed=not reasons, reasons=reasons, accuracy_verified=False,
        message='固定板拟合和留出姿态检查；舵机模型误差仍可能影响真实毫米精度，须独立位置检查')
