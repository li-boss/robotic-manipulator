"""人工/外部视觉共用的纯计算定位和抓放规划。禁止导入硬件库。"""
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np

from core import arm
from auto_geometry import (Robot, MODEL, array, positive, camera_model, rotation,
                           transform, segment_box_distance)
from auto_plan import arm_fingerprint
import cad_geometry

ASSETS = Path(__file__).parent / 'models/manipulation'


class PathEndpointError(ValueError):
    """A colliding endpoint cannot be repaired by changing the route."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def defaults():
    return dict(version=1, model_reviewed=False, calibration_reviewed=False,
        arm_config_sha256=None, scene_revision=None, observation_angles=None,
        observation_readings=None, grasp_reference_angles=None,
        table_z_mm=None, block_size_mm=None, block_size_id='block',
        jaw_open_width_mm=None, max_position_error_mm=None, max_direction_error_deg=None,
        grasp_height_mm=None, transfer_height_mm=None, release_gap_mm=None,
        workspace_xy_mm=None, motion_bounds_mm=None, obstacles=[], destinations={},
        camera=dict(matrix=None, distortion=None, image_size=[640,480], samples=[],
                    base_to_camera=None, anchor_frame=None,intrinsics_certificate=None),
        board=dict(points_mm=[], origin_in_base_mm=None, yaw_deg=None, anchor_pixels=[]),
        checks=[], path_validations=[], cad=None,
        model=dict(urdf_sha256=hashlib.sha256(MODEL.read_bytes()).hexdigest(),
            servo_zero_deg=[90]*5, servo_sign=[1]*5, tcp_xyz_mm=None, tcp_rpy_deg=None,
            jaw_axis_tcp=None, collision_radii_mm=None, tool_radius_mm=None,
            tool_boxes_mm=None, base_box_mm=None, measurements=[], independent_measurements=[]))


def calibration_version(cfg):
    # 浏览器会把1.0序列化为1；数值相同不应清除采用状态或使路径证据失效。
    def canonical(value):
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return [canonical(v) for v in value]
        if type(value) is float and math.isfinite(value) and value.is_integer():return int(value)
        return value
    return digest(canonical({k:v for k,v in cfg.items() if k not in
        ('model_reviewed','calibration_reviewed','arm_config_sha256','path_validations')}))


def pixel(cfg, value, name='像素'):
    p=array(value,(2,),name)
    size=array(cfg['camera']['image_size'],(2,),'图像尺寸')
    if np.any(p<0) or np.any(p>=size):raise ValueError(name+'超出原图')
    return p


def camera_pose(cfg,camera_angles=None):
    k,d=camera_model(cfg)
    pose=(cad_geometry.camera_extrinsics(cfg,cfg['observation_readings'] if camera_angles is None else camera_angles)
          if cfg.get('cad') is not None else cfg['camera']['base_to_camera'])
    if not isinstance(pose,dict):raise ValueError('尚未标定相机相对底座的位置')
    rvec=array(pose.get('rvec'),(3,),'相机旋转')
    t=array(pose.get('tvec_mm'),(3,),'相机平移')
    return k,d,cv2.Rodrigues(rvec)[0],t


def projection_offset(cfg):
    """相机投影平面相对实测桌面的高度差（用户10-29：手眼板面仅用于投影）。"""
    source=((cfg.get('cad') or {}).get('camera_calibration') or {}).get('projection_table')
    return float(source['z_mm'])-cfg['table_z_mm'] if source else 0.


def project_to_plane(cfg, value, height,camera_angles=None,offset=None):
    k,d,r,t=camera_pose(cfg,camera_angles)
    ray=np.r_[cv2.undistortPoints(pixel(cfg,value).reshape(1,1,2),k,d).reshape(2),1.]
    origin=-r.T@t; direction=r.T@ray
    if abs(direction[2])<1e-9:raise ValueError('视线与目标平面平行')
    # 在相机自洽的板面高度求交，平面坐标配以机械臂实测基准高度。
    distance=(height+(projection_offset(cfg) if offset is None else offset)-origin[2])/direction[2]
    if distance<=0:raise ValueError('目标平面位于相机后方')
    point=origin+distance*direction
    point[2]=height
    return point


def board_world(cfg):
    pts=cfg['board']['points_mm']
    if not isinstance(pts,list) or len(pts)<4:raise ValueError('至少需要四个编号明确的纸上角点，不能只点十字中心')
    local=array(pts,(len(pts),2),'标定纸角点坐标')
    if np.linalg.matrix_rank(local-local.mean(axis=0))<2:raise ValueError('标定角点不能共线')
    origin=array(cfg['board']['origin_in_base_mm'],(3,),'纸张原点相对底座的位置')
    yaw=cfg['board']['yaw_deg']
    if not arm.number(yaw):raise ValueError('请测量纸张相对底座的方向')
    if not arm.number(cfg['table_z_mm']) or abs(origin[2]-cfg['table_z_mm'])>.001:
        raise ValueError('平铺标定纸的高度须与已测桌面高度一致')
    return np.column_stack([local,np.zeros(len(local))])@rotation([0,0,1],math.radians(yaw)).T+origin


def fit_extrinsics(cfg, pixels):
    k,d=camera_model(cfg);xyz=board_world(cfg)
    pts=array(pixels,(len(xyz),2),'纸上角点像素')
    for p in pts:pixel(cfg,p.tolist())
    ok,r,t=cv2.solvePnP(xyz,pts,k,d,flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or np.any((cv2.Rodrigues(r)[0]@xyz.T+t).T[:,2]<=0):
        raise ValueError('相机外参求解无效')
    predicted=cv2.projectPoints(xyz,r,t,k,d)[0].reshape(-1,2)
    error=float(np.max(np.linalg.norm(predicted-pts,axis=1)))
    if error>1:raise ValueError('纸张角点最大重投影误差超过1像素')
    return dict(rvec=r.reshape(3).tolist(),tvec_mm=t.reshape(3).tolist(),max_reprojection_px=error)


def workspace(cfg):
    pts=cfg['workspace_xy_mm']
    if not isinstance(pts,list) or len(pts)<3:raise ValueError('请测量允许取放的桌面区域')
    poly=array(pts,(len(pts),2),'桌面区域').astype(np.float32)
    if not cv2.isContourConvex(poly) or abs(cv2.contourArea(poly))<1:
        raise ValueError('桌面区域须为按外围顺序填写的凸多边形')
    return poly


def inside(cfg, xy, inset=0):
    return cv2.pointPolygonTest(workspace(cfg),tuple(map(float,xy)),True)>=inset


def checked_snapshot(arms,rows):
    if len(rows)!=6 or any(r.get('read_ok') is not True for r in rows):raise ValueError('需要六关节真实完整读数')
    for i,r in enumerate(rows,1):
        if r.get('id')!=i:raise ValueError('关节读取顺序异常')
        arm.observed_angle(arms,i,r.get('angle'))
    return [r['angle'] for r in rows]


def observation(cfg, arms, rows):
    arms=cad_geometry.effective_arms(cfg,arms)
    raw=checked_snapshot(arms,rows)
    if cad_geometry.dynamic_capture(cfg):return raw
    targets=array(cfg['observation_angles'],(5,),'观察目标')
    reference=array(cfg['observation_readings'],(5,),'观察原始读数')
    if np.max(abs(np.asarray(raw[:5])-reference))>1:raise ValueError('不在标定观察姿态')
    if np.max(abs(reference-targets))>1:raise ValueError('观察读数与明确目标不符')
    if abs(raw[5]-arms['gripper']['open'])>1:raise ValueError('观察时夹爪须张开')


def parameters(cfg, arms):
    arms=cad_geometry.effective_arms(cfg,arms)
    block=array(cfg['block_size_mm'],(3,),'方块长宽高')
    if np.any(block<=0) or abs(block[0]-block[1])>1:raise ValueError('首版仅支持正方形顶面、已知尺寸的方块')
    if not cad_geometry.dynamic_capture(cfg):
        for key in ('observation_angles','grasp_reference_angles'):
            for i,v in enumerate(array(cfg[key],(5,),key),1):arm.angle(arms,i,float(v))
    raw=array(cfg['observation_readings'],(5,),'模型参数采样原始读数')
    for i,v in enumerate(raw,1):arm.observed_angle(arms,i,float(v))
    if not cad_geometry.dynamic_capture(cfg) and np.max(abs(raw-cfg['observation_angles']))>1:
        raise ValueError('观察读数与明确目标不符')
    for key in ('open','closed'):arm.angle(arms,6,arms.get('gripper',{}).get(key))
    if arms['gripper']['open']==arms['gripper']['closed']:raise ValueError('夹爪开合目标不能相同')
    arm.timing(arms);workspace(cfg)
    bounds=array(cfg['motion_bounds_mm'],(2,3),'已核对运动空间最小和最大坐标')
    if np.any(bounds[0]>=bounds[1]):raise ValueError('运动空间上下界无效')
    height=positive(cfg['grasp_height_mm'],'抓取中心离桌高度')
    if height>=block[2]:raise ValueError('抓取中心必须低于顶面')
    transfer=positive(cfg['transfer_height_mm'],'转移中心离桌高度')
    if transfer<=height+block[2]:raise ValueError('转移高度不足')
    positive(cfg['release_gap_mm'],'释放间隙')
    err=positive(cfg['max_position_error_mm'],'最大位置误差')
    degrees=positive(cfg['max_direction_error_deg'],'最大方向误差')
    if degrees>10:raise ValueError('方向误差阈值须不大于10度，并符合实测夹持余量')
    width=positive(cfg['jaw_open_width_mm'],'夹爪张开净宽')
    # err is a maximum displacement budget, not a standard deviation. Check
    # both sides of the opening directly; do not invent a six-sigma factor.
    projected=block[0]*(math.cos(math.radians(degrees))+math.sin(math.radians(degrees)))
    if (width-projected)/2<=err:raise ValueError('夹爪开口余量不足以覆盖方向和最大位置误差')
    if not isinstance(cfg['scene_revision'],str) or not cfg['scene_revision'].strip():raise ValueError('请填写本次场景核对名称')
    for name,xy in cfg['destinations'].items():
        if not isinstance(name,str) or not name.strip():raise ValueError('放置点名称无效')
        if not inside(cfg,array(xy,(2,),'固定放置点'),np.linalg.norm(block[:2])/2+err):raise ValueError('固定放置点超出区域')
    return dict(block=block.tolist(),position_error_mm=err,direction_error_deg=degrees)


SELF_SCALE_EDGE_TOLERANCE=.15
SELF_SCALE_MAX_SHIFT_MM=40.


def self_scale_offset(cfg,outline,z,angles,size):
    """用户10-29：按已知方块顶面边长求本帧投影平面修正；不满足条件返回None及原因。"""
    base=projection_offset(cfg)
    def edges(offset):
        points=[project_to_plane(cfg,p.tolist(),z,angles,offset)[:2] for p in outline]
        # 排序使结果与四角点选顺序无关。
        return np.sort([np.linalg.norm(points[i]-points[(i+1)%4]) for i in range(4)])
    low,high=base-SELF_SCALE_MAX_SHIFT_MM,base+SELF_SCALE_MAX_SHIFT_MM
    try:f_low,f_high=edges(low).mean()-size,edges(high).mean()-size
    except ValueError as exc:return None,str(exc)
    if f_low*f_high>0:return None,'本帧方块投影边长需修正超过'+str(int(SELF_SCALE_MAX_SHIFT_MM))+'毫米'
    for _ in range(60):
        middle=(low+high)/2;value=edges(middle).mean()-size
        if (value>0)==(f_high>0):high=middle
        else:low=middle;f_low=value
    offset=(low+high)/2;fitted=edges(offset)
    if np.any(abs(fitted-size)>size*SELF_SCALE_EDGE_TOLERANCE):
        return None,'四条顶面边自校后为'+'/'.join(f'{v:.1f}' for v in fitted)+'毫米，相差过大，请核对四角是否点在顶面角上'
    return offset,fitted


def locate(cfg, target):
    if not isinstance(target,dict) or target.get('source') not in ('manual','vision'):
        raise ValueError('需要人工或外部视觉目标')
    if target.get('image_size')!=cfg['camera']['image_size']:raise ValueError('目标分辨率与标定不符')
    if target.get('block_size_id')!=cfg['block_size_id']:raise ValueError('物体尺寸版本不匹配')
    box=array(target.get('bbox'),(4,),'目标框')
    lo=pixel(cfg,box[:2].tolist(),'框起点');hi=pixel(cfg,box[2:].tolist(),'框终点')
    if np.any(hi-lo<2):raise ValueError('目标框为空或过小')
    center=pixel(cfg,target.get('center_px'),'顶面中心')
    edge=array(target.get('edge_px'),(2,2),'顶面边的两个端点')
    for p in [center,*edge]:
        pixel(cfg,p.tolist())
        if np.any(p<lo) or np.any(p>hi):raise ValueError('中心和方向端点必须位于目标框内')
    outline=target.get('outline_px')
    if outline is not None:
        outline=array(outline,(4,2),'顶面四角')
        for p in outline:
            pixel(cfg,p.tolist(),'顶面角点')
            if np.any(p<lo) or np.any(p>hi):raise ValueError('顶面角点必须位于目标范围内')
        poly=outline.astype(np.float32)
        if not cv2.isContourConvex(poly) or abs(cv2.contourArea(poly))<4:
            raise ValueError('请沿顶面轮廓依次点四个不同角点，不能交叉或共线')
        if any(cv2.pointPolygonTest(poly,tuple(map(float,p)),True)<-1e-4 for p in [center,*edge]):
            raise ValueError('中心和边方向点应在四角围成的顶面内')
    if np.linalg.norm(edge[1]-edge[0])<2:raise ValueError('方向端点过近，请在同一条顶面边上选两个分开的点')
    block=array(cfg['block_size_mm'],(3,),'方块尺寸');z=cfg['table_z_mm']+block[2]
    angles=target.get('_frame_joint_angles') if cfg.get('cad') is not None else None
    if cad_geometry.dynamic_capture(cfg):array(angles,(5,),'冻结相机原始姿态')
    warnings=[];offset=None;source='calibrated';fitted=None
    if cad_geometry.dynamic_capture(cfg) and outline is not None:
        offset,fitted=self_scale_offset(cfg,outline,z,angles,float(block[0]))
        if offset is None:
            warnings.append('本帧方块尺寸自校未采用：'+fitted+'；使用手眼板面换算。');fitted=None
        else:source='block_size_fit'
    xyz=project_to_plane(cfg,center.tolist(),z,angles,offset)
    points=np.array([project_to_plane(cfg,p.tolist(),z,angles,offset) for p in edge])
    direction=points[1,:2]-points[0,:2];length=float(np.linalg.norm(direction))
    if not math.isfinite(length) or length<1e-6:raise ValueError('方向在桌面投影中退化，请重新指定边方向')
    # CAD size diagnostics are advisory camera/model checks, independent of
    # the user-selected IK position acceptance budget.
    mismatch=abs(length-block[0])>(2. if cfg.get('cad') is not None else cfg['max_position_error_mm']*2)
    if cfg.get('cad') is None:
        if length<max(1.,block[0]*.25):raise ValueError('方向端点过近，请点同一条顶面边的两端')
        if mismatch:raise ValueError('当前标定投影边长与已知方块尺寸不符，请检查点选和相机标定')
    elif mismatch:
        warnings.append(f'当前相机模型把方向线段投影为{length:.1f}毫米，已知方块边长{block[0]:.1f}毫米；模型和成像参数精度未实测，该差异仅作定位诊断，不判定标注错误。方块尺寸仍使用已知值，未自动缩放坐标或修改相机参数。')
    diagnostics=dict(edge_length_mm=length,expected_edge_length_mm=float(block[0]),warnings=warnings,
        projection_source=source,projection_offset_mm=float(projection_offset(cfg) if offset is None else offset),
        fitted_edges_mm=None if fitted is None else [float(v) for v in fitted])

    if not inside(cfg,xyz[:2],np.linalg.norm(block[:2])/2+cfg['max_position_error_mm']):raise ValueError('方块边缘超出已核对桌面区域')
    mode=target.get('task_mode','pick_place')
    if mode not in ('pickup','pick_place'):raise ValueError('未知抓放任务类型')
    if mode=='pickup':
        return dict(xyz_mm=xyz.tolist(),edge_yaw_deg=math.degrees(math.atan2(direction[1],direction[0])),
                    destination_xy_mm=xyz[:2].tolist(),destination_kind='none',task_mode=mode,**diagnostics)
    dest=target.get('destination',{})
    if dest.get('kind')=='fixed':
        if dest.get('name') not in cfg['destinations']:raise ValueError('固定放置点不存在')
        xy=array(cfg['destinations'][dest['name']],(2,),'固定放置点')
    elif dest.get('kind')=='pixel':xy=project_to_plane(cfg,dest.get('pixel'),cfg['table_z_mm'],angles,offset)[:2]
    else:raise ValueError('请选择固定点或桌面放置中心')
    if not inside(cfg,xy,np.linalg.norm(block[:2])/2+cfg['max_position_error_mm']):raise ValueError('放置后方块边缘超出区域')
    separation=np.linalg.norm(xy-xyz[:2]);footprint=np.linalg.norm(block[:2])
    # This is a nominal source/destination check for one object, which leaves
    # the source before placement. An allowed IK residual is not an observed
    # displacement to add to both footprints. Full swept geometry is separate.
    if separation<footprint:
        raise ValueError('取物和放置区域重叠，请选择另一处空区域')
    if separation<footprint+2*cfg['max_position_error_mm']:
        warnings.append('取放位置较近，请确认放置区域为空；运动仍须通过完整路径检查。')
    return dict(xyz_mm=xyz.tolist(),edge_yaw_deg=math.degrees(math.atan2(direction[1],direction[0])),
                destination_xy_mm=xy.tolist(),destination_kind=dest['kind'],task_mode=mode,**diagnostics)


class ManipulationRobot(Robot):
    """复用原连续扫掠与单关节搜索，仅增加方向和独立运动空间。"""
    def __init__(self,cfg,arms):
        inner=copy.deepcopy(cfg)
        bounds=array(cfg['motion_bounds_mm'],(2,3),'运动空间')
        x0,y0=bounds[0,:2];x1,y1=bounds[1,:2]
        inner['marks']={'xy_mm':[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]}
        super().__init__(inner,cad_geometry.effective_arms(cfg,arms))
        self.bounds=bounds
        base=cfg['model']['base_box_mm']
        if not isinstance(base,dict):raise ValueError('请核对底座碰撞包络盒')
        self.base_center=array(base.get('center_mm'),(3,),'底座包络中心')
        self.base_half=array(base.get('half_size_mm'),(3,),'底座包络半尺寸')
        if np.any(self.base_half<=0):raise ValueError('底座包络半尺寸须为正')
        self.jaw_axis=array(cfg['model']['jaw_axis_tcp'],(3,),'夹爪闭合方向（工具坐标）')
        if abs(np.linalg.norm(self.jaw_axis)-1)>1e-6:raise ValueError('夹爪方向须为实测单位向量')
        if abs(self.jaw_axis[2])>.001:raise ValueError('夹爪闭合方向须垂直于工具接近轴')
        if cfg.get('cad') is not None:
            cad_geometry.validate(cfg)
            self._cad_floor_support=[np.asarray(v) for v in cad_geometry.profile()['floor_support_vertices_mm']]
            boxes=[(i,b) for i,rows in enumerate(cfg['cad']['collision_boxes']) for b in rows]
            self._cad_box_links=np.array([i for i,_ in boxes])
            self._cad_box_centers=np.array([b['center_mm'] for _,b in boxes])
            self._cad_box_halves=np.array([b['half_size_mm'] for _,b in boxes])
            self._cad_box_radii=np.linalg.norm(self._cad_box_halves,axis=1)
            self._cad_reach=sum(np.linalg.norm(j[0][:3,3])*1000 for j in self.joints)+np.linalg.norm(self.tool[:3,3])*1000
        self._collision_cache={}

    def fk(self,values,strict=True,enforce_model=True):
        if cad_geometry.dynamic_capture(self.cfg) and not strict and enforce_model:
            q=array(values,(5,),'几何姿态')
            for i,(_,_,low,high) in enumerate(self.joints):
                theta=math.radians((q[i]-self.offset[i])*self.sign[i])
                if not low-math.radians(1)<=theta<=high+math.radians(1):
                    raise ValueError('起始或扫掠角度超出模型及1度读数容差')
            return super().fk(values,False,False)
        return super().fk(values,strict,enforce_model)

    def matrices(self,q):
        result=[np.eye(4)];m=result[0]
        for i,(origin,axis,_,_) in enumerate(self.joints):
            j=np.eye(4);j[:3,:3]=rotation(axis,math.radians((q[i]-self.offset[i])*self.sign[i]))
            m=m@origin@j;result.append(m)
        return [v.tolist() for v in result]

    def collision(self,values,carried=None,margin=0):
        if self.cfg.get('cad') is not None:
            key=(tuple(values),None if carried is None else tuple(np.asarray(carried).ravel()),margin)
            cached=self._collision_cache.get(key)
            if isinstance(cached,str):raise ValueError(cached)
            if cached is not None:return cached
            if len(self._collision_cache)>10000:self._collision_cache.clear()
            try:value=cad_geometry.collision(self,values,carried,margin)
            except ValueError as exc:self._collision_cache[key]=str(exc);raise
            self._collision_cache[key]=value
            return value
        m=super().collision(values,carried,margin)
        tcp=m[:3,3]*1000
        if np.any(tcp-margin<self.bounds[0]) or np.any(tcp+margin>self.bounds[1]):raise ValueError('末端超出实测运动空间')
        _,points=self.fk(values,False)
        wrist=m@np.linalg.inv(self.tool)
        # 与底座相连的第一段不作自碰撞配对，其余连杆和工具均检查底座。
        for a,b,r in list(zip(points[1:-1],points[2:],self.radii))[1:]:
            if segment_box_distance(a-self.base_center,b-self.base_center,self.base_half)<=r+margin:
                raise ValueError('非相邻连杆或末端接近底座包络')
        for a,b,r in zip(points[1:-1],points[2:],self.radii):
            if np.any(np.minimum(a,b)-r-margin<self.bounds[0]) or np.any(np.maximum(a,b)+r+margin>self.bounds[1]):
                raise ValueError('连杆包络超出实测运动空间')
        for center,half in self.boxes:
            world=wrist[:3,:3]@center+wrist[:3,3]*1000;extent=abs(wrist[:3,:3])@half+margin
            if np.any(world-extent<self.bounds[0]) or np.any(world+extent>self.bounds[1]):raise ValueError('工具包络超出实测运动空间')
            if np.all(abs(world-self.base_center)<=self.base_half+extent):raise ValueError('工具接近底座包络')
        if carried is not None:
            pose=m@carried;extent=abs(pose[:3,:3])@(np.asarray(self.cfg['block_size_mm'])/2)+margin
            if np.any(pose[:3,3]*1000-extent<self.bounds[0]) or np.any(pose[:3,3]*1000+extent>self.bounds[1]):
                raise ValueError('携带方块超出实测运动空间')
            if np.all(abs(pose[:3,3]*1000-self.base_center)<=self.base_half+extent):raise ValueError('携带方块接近底座包络')
        for obstacle in self.cfg['obstacles']:
            center=array(obstacle['center_mm'],(3,),'障碍中心')
            half=array(obstacle['half_size_mm'],(3,),'障碍半尺寸')
            if np.any(half<=0):raise ValueError('障碍半尺寸无效')
            for a,b,r in zip(points[1:-1],points[2:],self.radii):
                if segment_box_distance(a-center,b-center,half)<=r+margin:raise ValueError('连杆接近已登记障碍')
            for c,h in self.boxes:
                world=wrist[:3,:3]@c+wrist[:3,3]*1000
                if np.all(abs(world-center)<=half+abs(wrist[:3,:3])@h+margin):raise ValueError('工具接近已登记障碍')
            if carried is not None:
                pose=m@carried;block=np.asarray(self.cfg['block_size_mm'])/2
                extent=abs(pose[:3,:3])@block
                if np.all(abs(pose[:3,3]*1000-center)<=half+extent+margin):raise ValueError('携带方块接近已登记障碍')
        return m

    def collision_samples(self,values,carried=None,margin=0):
        from manipulation_collision import check_samples
        return check_samples(self,values,carried,margin)

    def transition(self,start,end,carried=None):
        if cad_geometry.dynamic_capture(self.cfg):self.fk(end,True)
        cad=self.cfg.get('cad') is not None
        if cad:
            margin=self._cad_reach*math.radians(1.25)
            for label,pose in (('起始',start),('目标',end)):
                try:self.collision(pose,carried,margin)
                except ValueError as exc:raise PathEndpointError(label+'姿态不满足碰撞包络：'+str(exc)) from exc
        try:return super().transition(start,end,carried,depth_first=cad)
        except ValueError as original:
            if self.cfg.get('cad') is None:raise
            # 只对已合法的端点构造有限整数路点；每段仍逐关节做连续扫掠。
            # 这不是把非法目标截断成合法目标，也不会在搜索期间发送运动。
            for step in (5,2,1):
                count=max(1,int(math.ceil(max(abs(np.asarray(end)-start))/step)))
                current=list(start);commands=[]
                try:
                    for n in range(1,count+1):
                        waypoint=[int(round(a+(b-a)*n/count)) for a,b in zip(start,end)]
                        for i,v in enumerate(waypoint,1):arm.angle(self.arm,i,v)
                        commands+=super().transition(current,waypoint,carried,depth_first=True);current=waypoint
                    return commands
                except ValueError:continue
            raise original

    # Controller.write按关节编号依次发出，每条之间约20 ms，用时相同。
    SEND_GAP_MS=20

    def sync_transition(self,start,end,carried=None):
        """一次批量下发的同步路径；检查线性插值及发送滞后包络，不通过即抛错。"""
        if cad_geometry.dynamic_capture(self.cfg):self.fk(end,True)
        for i,v in enumerate(end,1):arm.angle(self.arm,i,v)
        q0=np.asarray(start,float);q1=np.asarray(end,float)
        changed=[i for i in range(5) if end[i]!=start[i]]
        if not changed:return []
        margin=self._cad_reach*math.radians(1.25)
        count=max(1,int(math.ceil(max(abs(q1-q0))/.25)))
        fraction=np.linspace(0,1,count+1)
        self.collision_samples(q0+np.outer(fraction,q1-q0),carried,margin)
        if len(changed)==1:return [[changed[0]+1,end[changed[0]]]]
        # 第k个发出的关节晚k个间隔起步；舵机曲线未实测，此为保守采样包络。
        # 发送顺序即Controller.write的下发顺序，按编号优先尝试，找到整段通过的顺序。
        lag=self.SEND_GAP_MS/arm.timing(self.arm)[0]
        time_axis=np.linspace(0,1+lag*(len(changed)-1),count+len(changed))
        failure=None
        for order in itertools.permutations(changed):
            lagged=np.repeat(q0[None],len(time_axis),axis=0)
            for rank,joint in enumerate(order):
                lagged[:,joint]=q0[joint]+np.clip(time_axis-rank*lag,0,1)*(q1[joint]-q0[joint])
            try:self.collision_samples(lagged,carried,margin)
            except ValueError as exc:failure=exc;continue
            return [[joint+1,end[joint]] for joint in order]
        raise ValueError('同步下发的发送滞后包络未通过：'+str(failure))

    # 衔接：上一段结束前该毫秒数下发下一段（用户10-29第三次要求）。
    BLEND_LEAD_MS=300

    def blend_check(self,start,middle,group,carried=None):
        """检查提前下发下一组造成的切角路径；舵机曲线未实测，按多个提前比例保守采样。"""
        q0=np.asarray(start,float);q1=np.asarray(middle,float);q2=q1.copy()
        for joint,value in group:
            if joint<=5:q2[joint-1]=value
        duration=arm.timing(self.arm)[0];lead=self.BLEND_LEAD_MS/duration
        lag=self.SEND_GAP_MS/duration
        order=[joint-1 for joint,_ in group if joint<=5]
        margin=self._cad_reach*math.radians(1.25);trials=[]
        for fraction in (1-1.5*lead,1-lead,1-.5*lead):
            cut=q0+max(fraction,0)*(q1-q0)
            count=max(1,int(math.ceil(max(abs(q2-cut))/.25)))
            time_axis=np.linspace(0,1+lag*max(len(order)-1,0),count+len(order)+1)
            path=np.repeat(cut[None],len(time_axis),axis=0)
            for rank,joint in enumerate(order):
                path[:,joint]=cut[joint]+np.clip(time_axis-rank*lag,0,1)*(q2[joint]-cut[joint])
            trials.append(path)
        self.collision_samples(np.concatenate(trials),carried,margin)

    def grouped_transition(self,start,end,carried=None):
        """返回批量指令组：同步直达 → 经零点同步 → 原逐关节路径（每组一个关节）。"""
        if self.cfg.get('cad') is not None:
            if cad_geometry.dynamic_capture(self.cfg):self.fk(end,True)
            margin=self._cad_reach*math.radians(1.25)
            for label,pose in (('起始',start),('目标',end)):
                try:self.collision(pose,carried,margin)
                except ValueError as exc:raise PathEndpointError(label+'姿态不满足碰撞包络：'+str(exc)) from exc
            try:
                group=self.sync_transition(start,end,carried)
                return [group] if group else []
            except ValueError:pass
            neutral=[int(v) for v in self.offset]
            if all(a==b for a,b in zip(neutral,self.offset)) and list(start)!=neutral and list(end)!=neutral:
                try:
                    groups=[self.sync_transition(start,neutral,carried),self.sync_transition(neutral,end,carried)]
                    return [g for g in groups if g]
                except ValueError:pass
        return [[command] for command in self.transition(start,end,carried)]

    def ik_directed(self,xyz,seed,yaw,tilt_deg=0,held_wrist=None):
        target=array(xyz,(3,),'目标')/1000
        if not arm.number(tilt_deg) or not 0<=tilt_deg<=45:raise ValueError('接近倾角须为0～45度')
        theta=math.radians(tilt_deg);radial=np.asarray(xyz[:2],float)
        if np.linalg.norm(radial)<1e-6:raise ValueError('目标在底座旋转轴上，接近方向退化')
        approach=np.r_[radial/np.linalg.norm(radial)*math.sin(theta),-math.cos(theta)]
        bounds=np.array([self.arm['joint_limits'][str(i)] for i in range(1,6)])
        if held_wrist is not None:
            arm.angle(self.arm,5,held_wrist);bounds[4]=[held_wrist,held_wrist]
        choices=[]
        for yaw_offset in (0,90,180,270):
            desired=np.array([math.cos(math.radians(yaw+yaw_offset)),math.sin(math.radians(yaw+yaw_offset))])
            def residual(q):
                m,_=self.fk(q,False);jaw=(m[:3,:3]@self.jaw_axis)[:2]
                if np.linalg.norm(jaw)<1e-8:raise ValueError('夹爪方向投影退化')
                return np.r_[m[:3,3]-target,.06*(m[:3,2]-approach),.04*(jaw/np.linalg.norm(jaw)-desired)]
            middle=np.asarray(seed,float).copy();middle[4]=sum(bounds[4])/2
            seeds=[np.asarray(seed,float),middle]
            if self.cfg.get('grasp_reference_angles') is not None:seeds.insert(1,np.asarray(self.cfg['grasp_reference_angles'],float))
            # 课程五轴链的平面二连杆解析种子，之后仍做完整FK和整数化复核。
            # 仅枚举实际满足范围的根，不截断或修正越界目标。
            l1=np.linalg.norm(self.joints[2][0][:3,3]);l2=np.linalg.norm(self.joints[3][0][:3,3])
            l3=abs(self.joints[4][0][0,3]);side=abs(self.joints[4][0][1,3]);tool=self.tool[2,3]
            a=math.atan2(-target[0],target[1]);S=-math.pi+theta
            yy=np.linalg.norm(target[:2])-tool*math.sin(theta)-l3*math.sin(theta)-side*math.cos(theta)
            zz=target[2]+tool*math.cos(theta)-sum(j[0][2,3] for j in self.joints[:2])+l3*math.cos(theta)-side*math.sin(theta)
            cosine=(yy*yy+zz*zz-l1*l1-l2*l2)/(2*l1*l2)
            if -1<=cosine<=1:
                relative=math.radians(yaw+yaw_offset)-a
                wrist=math.atan2(math.sin(relative)/math.cos(S),math.cos(relative))
                for elbow in (math.acos(cosine),-math.acos(cosine)):
                    shoulder=math.atan2(-yy,zz)-math.atan2(l2*math.sin(elbow),l1+l2*math.cos(elbow))
                    for winding in (-1,0,1):
                        physical=np.degrees([a,shoulder,elbow,S-shoulder-elbow,wrist+2*math.pi*winding])
                        candidate=self.offset+physical/self.sign
                        if held_wrist is not None:candidate[4]=held_wrist
                        if np.all(candidate>=bounds[:,0]) and np.all(candidate<=bounds[:,1]):seeds.insert(0,candidate)
            for initial in seeds:
                q=initial.copy()
                if held_wrist is not None:q[4]=held_wrist
                try:
                    for _ in range(160):
                        e=residual(q)
                        jac=np.column_stack([(residual(q+np.eye(5)[i]*.001)-e)/.001 for i in range(5)])
                        delta=-jac.T@np.linalg.solve(jac@jac.T+np.eye(len(e))*1e-7,e)
                        # 有界最小二乘：边界上的向外搜索方向冻结，重新求自由变量。
                        # 不截断越界解，也不把搜索中间值下发为目标。
                        free=np.ones(5,dtype=bool)
                        for _active in range(5):
                            outward=((q<=bounds[:,0]+1e-8)&(delta<0))|((q>=bounds[:,1]-1e-8)&(delta>0))
                            if not np.any(outward&free):break
                            free[outward]=False
                            jf=jac[:,free];delta=np.zeros(5)
                            delta[free]=-jf.T@np.linalg.solve(jf@jf.T+np.eye(len(e))*1e-7,e)
                        accepted=False
                        for scale in (1,.5,.25,.125,.0625,.03125):
                            proposal=q+delta*scale
                            if np.any(proposal<bounds[:,0]) or np.any(proposal>bounds[:,1]):continue
                            try:en=residual(proposal)
                            except ValueError:continue
                            if np.linalg.norm(en)<np.linalg.norm(e)-1e-12:q=proposal;accepted=True;break
                        if not accepted:break
                    for integer in itertools.product(*[sorted({math.floor(v),math.ceil(v)}) for v in q]):
                        try:m,_=self.fk(list(integer))
                        except ValueError:continue
                        jaw=(m[:3,:3]@self.jaw_axis)[:2]
                        if np.linalg.norm(jaw)<1e-8:continue
                        angle=math.degrees(math.acos(float(np.clip(jaw@desired/np.linalg.norm(jaw),-1,1))))
                        error=float(np.linalg.norm(m[:3,3]-target)*1000)
                        if (error<=self.cfg['max_position_error_mm'] and angle<=self.cfg['max_direction_error_deg']
                                and np.linalg.norm(m[:3,2]-approach)<=.035):
                            choices.append((error+angle*.1+np.linalg.norm(np.asarray(integer)-seed)*.001,list(integer)))
                except (ValueError,np.linalg.LinAlgError):continue
        if not choices:raise ValueError('位置或夹爪方向不可达，或整数角度误差超标')
        return min(choices)[1]


_REACH_CACHE={}


def reach_range(cfg,arms,height,max_tilt_deg=80):
    """模型在给定TCP高度的水平伸展范围（毫米，未计碰撞，为上限）；底座与腕转角不影响半径。"""
    from manipulation_ik import batch_fk
    robot=ManipulationRobot(cfg,arms);limits=robot.arm['joint_limits']
    key=(robot.digest,round(float(height),3),max_tilt_deg,json.dumps(limits,sort_keys=True))
    if key in _REACH_CACHE:return _REACH_CACHE[key]
    grids=[np.arange(limits[str(i)][0],limits[str(i)][1]+1,3,dtype=float) for i in (2,3,4)]
    q2,q3,q4=[g.ravel() for g in np.meshgrid(*grids,indexing='ij')]
    base=float(np.mean(limits['1']));wrist=float(np.mean(limits['5']))
    radii=[];cosine=math.cos(math.radians(max_tilt_deg))
    for start in range(0,len(q2),40000):
        qs=np.column_stack([np.full(len(q2[start:start+40000]),base),q2[start:start+40000],q3[start:start+40000],q4[start:start+40000],np.full(len(q2[start:start+40000]),wrist)])
        m=batch_fk(robot,qs)
        keep=(abs(m[:,2,3]*1000-height)<=3)&(-m[:,2,2]>=cosine)
        radii.extend(np.linalg.norm(m[keep,:2,3],axis=1)*1000)
    result=(float(min(radii)),float(max(radii))) if radii else None
    _REACH_CACHE[key]=result
    return result


def check_reach(cfg,arms,located):
    """用户10-29：取放点超出模型伸展上限时直接说明，而不是报逆解候选失败。"""
    if cfg.get('cad') is None:return
    z=cfg['table_z_mm']
    rows=[('取物点',located['xyz_mm'][:2],z+cfg['grasp_height_mm'])]
    if located['task_mode']=='pick_place':rows.append(('放置点',located['destination_xy_mm'],z+cfg['grasp_height_mm']+cfg['release_gap_mm']))
    for name,xy,height in rows:
        limits=reach_range(cfg,arms,height);r=float(np.linalg.norm(xy))
        if limits is None:raise ValueError(name+'所在高度超出机械臂模型可达范围')
        if not limits[0]<=r<=limits[1]:
            raise ValueError(f'{name}距底座{r:.0f}毫米，超出机械臂在夹取高度的伸展范围约{limits[0]:.0f}～{limits[1]:.0f}毫米（模型上限，未计碰撞，实际更小）；请把方块或放置点移到该范围内')


def model_report(cfg,arms):
    robot=ManipulationRobot(cfg,arms);errors={}
    if cfg.get('cad') is not None:
        return dict(source='CAD',accuracy_verified=False,assumptions=cfg['cad']['notes'])
    for field,minimum in [('measurements',3),('independent_measurements',1)]:
        rows=cfg['model'][field]
        if not isinstance(rows,list) or len(rows)<minimum:raise ValueError('请记录至少三个模型核对姿态及一个独立姿态')
        errors[field]=[]
        for row in rows:
            m,_=robot.fk(row['angles']);value=float(np.linalg.norm(m[:3,3]*1000-array(row['xyz_mm'],(3,),'实测夹持中心')))
            if value>cfg['max_position_error_mm']:raise ValueError('模型夹持中心与实测误差超标')
            errors[field].append(value)
    poses=[tuple(r['angles']) for field in errors for r in cfg['model'][field]]
    if len(set(poses))!=len(poses):raise ValueError('模型与独立核对姿态不能重复')
    return errors


def robot_description(cfg,arms):
    """导出含TCP的审阅URDF；CAD来源不冒充实测，不启动ROS控制节点。"""
    robot=ManipulationRobot(cfg,arms);tree=ET.parse(ASSETS/'dofbot_display.urdf');root=tree.getroot()
    for link in root.findall('link'):
        for collision in list(link.findall('collision')):link.remove(collision)
    def shape(link,xyz,kind,attrs,rpy=(0,0,0)):
        collision=ET.SubElement(link,'collision');ET.SubElement(collision,'origin',xyz=' '.join(map(str,xyz)),rpy=' '.join(map(str,rpy)))
        geometry=ET.SubElement(collision,'geometry');ET.SubElement(geometry,kind,**attrs)
    for i in range(1,5):
        link=root.find(f"link[@name='link{i}']");v=robot.joints[i][0][:3,3];length=float(np.linalg.norm(v));radius=float(robot.radii[i-1]/1000)
        shape(link,[0,0,0],'sphere',dict(radius=str(radius)))
        shape(link,v.tolist(),'sphere',dict(radius=str(radius)))
        if length:
            rpy=[0,math.acos(float(v[2]/length)),math.atan2(v[1],v[0])]
            shape(link,(v/2).tolist(),'cylinder',dict(radius=str(radius),length=str(length)),rpy)
    shape(root.find("link[@name='base_link']"),(robot.base_center/1000).tolist(),'box',dict(size=' '.join(map(str,robot.base_half*2/1000))))
    wrist=root.find("link[@name='link5']")
    shape(wrist,[0,0,0],'sphere',dict(radius=str(max(robot.tool_radius,robot.radii[4])/1000)))
    for center,half in robot.boxes:shape(wrist,(center/1000).tolist(),'box',dict(size=' '.join(map(str,half*2/1000))))
    if cfg.get('cad') is not None:
        for link in root.findall('link'):
            for collision in list(link.findall('collision')):link.remove(collision)
        for index,boxes in enumerate(cfg['cad']['collision_boxes']):
            link=root.find("link[@name='%s']"%('base_link' if index==0 else 'link'+str(index)))
            for box in boxes:
                shape(link,(np.asarray(box['center_mm'])/1000).tolist(),'box',
                      dict(size=' '.join(map(str,np.asarray(box['half_size_mm'])*2/1000))))
    for i in range(1,6):
        low,high=arms['joint_limits'][str(i)]
        for value in (low,high):arm.angle(arms,i,value)
        values=[math.radians((v-robot.offset[i-1])*robot.sign[i-1]) for v in (low,high)]
        limit=root.find(f"joint[@name='joint{i}']/limit")
        lower=max(float(limit.get('lower')),min(values));upper=min(float(limit.get('upper')),max(values))
        if lower>upper:raise ValueError('配置角度与模型角度没有交集')
        limit.set('lower',str(lower));limit.set('upper',str(upper))
    ET.SubElement(root,'link',name='tcp')
    joint=ET.SubElement(root,'joint',name='cad_tcp' if cfg.get('cad') is not None else 'measured_tcp',type='fixed')
    ET.SubElement(joint,'parent',link='link5');ET.SubElement(joint,'child',link='tcp')
    ET.SubElement(joint,'origin',xyz=' '.join(str(v/1000) for v in cfg['model']['tcp_xyz_mm']),
                  rpy=' '.join(str(math.radians(v)) for v in cfg['model']['tcp_rpy_deg']))
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def calibration_report(cfg):
    camera_pose(cfg);errors=[];table=[];top=[]
    if cfg.get('cad') is not None:
        return dict(source=cfg['cad']['intrinsics_source'],accuracy_verified=False,
                    camera_calibration=cfg['cad'].get('camera_calibration'),
                    message='随臂定位使用已采用的相机参数与模型；拟合不代表独立空间精度或抓放已验收')
    for row in cfg['checks']:
        z=cfg['table_z_mm']+(cfg['block_size_mm'][2] if row['kind']=='top' else 0)
        expected=array(row['xy_mm'],(2,),'独立检查坐标')
        value=float(np.linalg.norm(project_to_plane(cfg,row['pixel'],z)[:2]-expected))
        if value>cfg['max_position_error_mm']:raise ValueError('独立定位误差超标')
        errors.append(value);(top if row['kind']=='top' else table).append(expected)
    if len(table)<3 or len(top)<3:raise ValueError('请各记录至少三个独立桌面和顶面检查点')
    for points in (table,top):
        if np.linalg.matrix_rank(np.asarray(points)-np.mean(points,axis=0))<2:raise ValueError('检查点须分散且不共线')
    return dict(errors_mm=errors)


def readiness(cfg,arms,require_path=False):
    issues=[];reports={}
    for name,fn in [('parameters',lambda:parameters(cfg,arms)),('model',lambda:model_report(cfg,arms)),('calibration',lambda:calibration_report(cfg))]:
        try:reports[name]=fn()
        except (ValueError,TypeError,KeyError,IndexError,cv2.error) as exc:issues.append(dict(item=name,reason=str(exc)))
    if cfg.get('cad') is not None:
        if not cad_geometry.dynamic_capture(cfg) and cfg['cad'].get('assumptions_confirmed') is not True:issues.append(dict(item='cad',reason='请核对并采用列出的CAD候选参数；这不登记实测标定'))
    else:
        for name in ('model_reviewed','calibration_reviewed'):
            if cfg.get(name) is not True:issues.append(dict(item=name,reason='尚未完成现场核对'))
    if cfg.get('arm_config_sha256')!=arm_fingerprint(arms):issues.append(dict(item='arms',reason='机械臂配置未绑定或已改变'))
    valid=[r for r in cfg['path_validations'] if r.get('version')==calibration_version(cfg) and r.get('real') is True and r.get('passed') is True]
    positions=np.asarray([r['xy_mm'] for r in valid])
    if require_path and not cad_geometry.dynamic_capture(cfg) and (len(valid)<5 or len({tuple(r['xy_mm']) for r in valid})<5 or np.linalg.matrix_rank(positions-positions.mean(axis=0))<2):issues.append(dict(item='paths',reason='至少五个分散位置的真实空载逐步路径尚未通过'))
    return dict(ready=not issues,issues=issues,reports=reports)


def build_plan(cfg,arms,target,require_path=True,progress=None,reference=None):
    if reference is not None:
        if cfg.get('cad') is None:raise ValueError('九点引导仅适用于当前CAD人工目标模式')
        from manipulation_guided import build_guided
        return build_guided(cfg,arms,target,require_path,reference,progress)
    # 对CAD模式枚举机构可实现的径向接近角；每个候选均检查完整扫掠。
    if cfg.get('cad') is not None:
        check_reach(cfg,arms,locate(cfg,target))
        failures=[]
        for tilt in (0,15,30,45):
            if progress:progress('尝试接近角 '+str(tilt)+'°')
            try:return _build_plan(cfg,arms,target,require_path,tilt,progress)
            except ValueError as exc:failures.append(str(tilt)+'°：'+str(exc))
        raise ValueError('所有接近姿态均不可用；'+'；'.join(failures))
    return _build_plan(cfg,arms,target,require_path,0,progress)


def _build_plan(cfg,arms,target,require_path,tilt,progress=None,guided=None):
    ready=readiness(cfg,arms,require_path)
    if not ready['ready']:raise ValueError('；'.join(x['reason'] for x in ready['issues']))
    located=locate(cfg,target);robot=guided.robot if guided is not None else ManipulationRobot(cfg,arms)
    arms=cad_geometry.effective_arms(cfg,arms)
    dynamic=cad_geometry.dynamic_capture(cfg)
    if dynamic:
        initial=array(target.get('_start_joint_angles'),(6,),'冻结六关节起始读数').tolist()
        checked_snapshot(arms,[dict(id=i,angle=v,read_ok=True) for i,v in enumerate(initial,1)])
        if initial[:5]!=target.get('_frame_joint_angles'):raise ValueError('起始姿态与冻结相机姿态不符')
    else:initial=list(cfg['observation_angles'])+[arms['gripper']['open']]
    current=initial[:5].copy();duration,settle=arm.timing(arms)
    steps=[];poses=[robot.matrices(current)];attachment=None;starts=[];carries=[]
    block=np.asarray(cfg['block_size_mm']);z=cfg['table_z_mm']
    def grip(label,key):
        starts.append(current.copy());carries.append(attachment)
        steps.append(dict(label=label,joints=[[6,arm.angle(arms,6,arms['gripper'][key])]],motion_ms=duration,settle_ms=settle))
        poses.append(robot.matrices(current))
    grip_base=None;grip_wrist=None;grip_tilt=None;grip_yaw=None
    def append(label,groups):
        nonlocal current
        for group in groups:
            starts.append(current.copy());carries.append(attachment)
            for joint,value in group:current[joint-1]=value
            steps.append(dict(label=label,joints=[list(pair) for pair in group],motion_ms=duration,settle_ms=settle))
            poses.append(robot.matrices(current))
    def move(label,xyz=None,q=None,keep_wrist=False):
        if progress:progress(str(tilt)+'°：'+label+'，计算逆解')
        yaw=located['edge_yaw_deg']
        if attachment is not None and grip_base is not None:
            # 保持夹持后的工具滚转关系，让方块随底座只绕桌面法向旋转。
            yaw=grip_yaw+math.degrees(math.atan2(-xyz[0],xyz[1]))-grip_base
        held=grip_wrist if attachment is not None else (current[4] if keep_wrist else None)
        free_yaw=yaw
        if keep_wrist and attachment is None:
            # 保持当前夹爪朝向竖直撤离。
            m,_=robot.fk(current);jaw=m[:3,:3]@robot.jaw_axis;yaw=math.degrees(math.atan2(jaw[1],jaw[0]))
        if guided is not None and q is None:
            try:groups,end,selected_tilt=guided.move(label,xyz,current,yaw,attachment,held,grip_tilt,force_tilt=label=='下降放置')
            except ValueError:
                # 撤离时优先保持腕部不转；无合法候选才按原方式求解。
                if not keep_wrist:raise
                groups,end,selected_tilt=guided.move(label,xyz,current,free_yaw,attachment,None,grip_tilt)
            append(label,groups)
            return selected_tilt
        try:end=list(q) if q is not None else robot.ik_directed(xyz,current,yaw,tilt,held)
        except ValueError as exc:
            if not keep_wrist:raise ValueError(label+'：'+str(exc)) from exc
            try:end=robot.ik_directed(xyz,current,free_yaw,tilt,None)
            except ValueError as again:raise ValueError(label+'：'+str(again)) from again
        if progress:progress(str(tilt)+'°：'+label+'，检查同步扫掠路径')
        try:groups=robot.grouped_transition(current,end,attachment)
        except PathEndpointError as exc:raise ValueError(label+'：'+str(exc)) from exc
        except ValueError:
            if cfg.get('cad') is None:raise
            # 由URDF零点构造中间姿态候选；只有整条路径通过才列入确认计划。
            neutral=[int(v) for v in robot.offset]
            if any(a!=b for a,b in zip(neutral,robot.offset)):raise ValueError('模型零点不是合法整数中间姿态')
            groups=robot.grouped_transition(current,neutral,attachment)+robot.grouped_transition(neutral,end,attachment)
        append(label,groups)
        return tilt
    xy=located['xyz_mm'][:2];dest=located['destination_xy_mm']
    pickup=[*xy,z+cfg['grasp_height_mm']];above=[*xy,z+cfg['transfer_height_mm']]
    drop=[*dest,z+cfg['grasp_height_mm']+cfg['release_gap_mm']];dest_above=[*dest,z+cfg['transfer_height_mm']]
    grip('张开夹爪','open');move('到取物上方',above)
    if cfg.get('cad') is not None and len(steps)>1 and steps[1]['label']=='到取物上方':
        # 碰撞模型按张开夹爪包络计算，夹爪张开与第一段移动同批下发。
        first=steps.pop(0);poses.pop(1);starts.pop(0);carries.pop(0)
        steps[0].update(label='张开夹爪并到取物上方',joints=steps[0]['joints']+first['joints'])
    grip_tilt=move('接近抓取',pickup);grip('夹持方块','closed')
    m,_=robot.fk(current);block_pose=transform([*np.asarray(xy)/1000,(z+block[2]/2)/1000],[0,0,math.radians(located['edge_yaw_deg'])])
    attachment=np.linalg.inv(m)@block_pose;grasp_attachment=attachment.copy()
    base_pose=np.asarray(robot.matrices(current))[1]
    grip_base=math.degrees(math.atan2(base_pose[1,0],base_pose[0,0]))
    jaw=m[:3,:3]@robot.jaw_axis
    grip_yaw=math.degrees(math.atan2(jaw[1],jaw[0]))
    grip_wrist=current[4]
    if guided is not None and located['task_mode']=='pick_place':drop=guided.drop_goal(current,m)['xyz']
    move('抬升方块',above)
    if located['task_mode']=='pick_place':
        move('转移至放置点上方',dest_above);move('下降放置',drop)
        released_pose=robot.fk(current)[0]@attachment
        placement_error=float(np.linalg.norm(released_pose[:3,3]*1000-np.r_[dest,z+block[2]/2+cfg['release_gap_mm']]))
        if guided is not None and placement_error>cfg['max_position_error_mm']:
            raise ValueError('方块释放位置误差'+str(round(placement_error,2))+'毫米超过当前允许值')
        if np.linalg.norm(released_pose[:3,2]-[0,0,1])>math.sin(math.radians(cfg['max_direction_error_deg'])):
            raise ValueError('五轴机构无法保持本次方块平放；请选择其他放置点或取物方向')
        grip('释放方块','open');attachment=None
        move('撤离放置点',dest_above,keep_wrist=True)
        if not dynamic:move('返回观察姿态',q=cfg['observation_angles'])
    else:
        released_pose=robot.fk(current)[0]@attachment
    if cfg.get('cad') is not None:
        # 相邻移动段提前衔接；切角路径未通过检查的位置保持停稳回读。
        transit={('张开夹爪并到取物上方','接近抓取'),('到取物上方','接近抓取'),('抬升方块','转移至放置点上方'),('转移至放置点上方','下降放置')}
        for i in range(len(steps)-1):
            a,b=steps[i],steps[i+1]
            moving=all(any(j<=5 for j,_ in x['joints']) for x in (a,b))
            if not moving or not (a['label']==b['label'] or (a['label'],b['label']) in transit):continue
            if progress:progress('检查'+a['label']+'→'+b['label']+'衔接切角路径')
            try:robot.blend_check(starts[i],starts[i+1],b['joints'],carries[i])
            except ValueError:continue
            a['blend_next_ms']=robot.BLEND_LEAD_MS
    if not 0<len(steps)<=200:raise ValueError('完整任务最多200条有限指令')
    for s in steps:
        for joint,value in s['joints']:arm.angle(arms,joint,value)
        arm.timing(dict(arms,motion_ms=s['motion_ms'],settle_ms=s['settle_ms']))
    return dict(strategy='manipulation',target=located,warnings=located['warnings'],steps=steps,poses=poses,
        initial_angles=initial,capture_mode='joint_snapshot_v1' if dynamic else 'fixed_observation',
        duration_ms=sum(s['motion_ms']-s['blend_next_ms'] if s.get('blend_next_ms') else s['motion_ms']+s['settle_ms'] for s in steps),
        calibration_version=calibration_version(cfg),model_sha256=robot.digest,
        tool_matrix=robot.tool.tolist(),joints=[dict(origin=o.tolist(),axis=a.tolist()) for o,a,_,_ in robot.joints],
        servo_zero_deg=robot.offset.tolist(),servo_sign=robot.sign.tolist(),
        block_initial_pose=block_pose.tolist(),block_attachment=grasp_attachment.tolist(),
        block_release_pose=released_pose.tolist(),
        placement_error_mm=placement_error if located['task_mode']=='pick_place' else None,
        geometry_source='cad_estimate' if cfg.get('cad') is not None else 'measured',
        approach_tilt_deg=grip_tilt,
        accuracy_verified=cfg.get('cad') is None,
        message='冻结人工/外部目标；'+('夹取抬升后保持，不自动松爪或返回' if located['task_mode']=='pickup' else '完整有限抓放')+'；真实结果需人工确认')
