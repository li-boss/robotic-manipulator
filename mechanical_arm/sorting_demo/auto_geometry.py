"""纯计算：针孔投影、课程URDF运动学与保守几何检查。无硬件导入。"""
import hashlib
import math
import itertools
from collections import deque
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
from core import arm

MODEL = Path(__file__).parent / 'models/course/dofbot.urdf'


def array(value, shape, name):
    if isinstance(value,np.ndarray):value=value.tolist()
    # 布尔值和字符串不应成为隐式测量值。
    def numbers(v):
        return all(numbers(x) for x in v) if isinstance(v, list) else arm.number(v)
    if not numbers(value):
        raise ValueError(name+'必须是有限数值，不接受空值、布尔值或字符串')
    a = np.asarray(value, dtype=float)
    if a.shape != shape or not np.isfinite(a).all():
        raise ValueError(name+'维度或数值无效')
    return a


def positive(value, name):
    if not arm.number(value) or value <= 0:
        raise ValueError('请填写正数：'+name)
    return float(value)


def rotation(axis, theta):
    axis = np.asarray(axis, float)
    axis /= np.linalg.norm(axis)
    x,y,z = axis
    skew = np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
    return np.eye(3)+math.sin(theta)*skew+(1-math.cos(theta))*(skew@skew)


def transform(xyz, rpy):
    m = np.eye(4)
    m[:3,3] = xyz
    m[:3,:3] = rotation([0,0,1],rpy[2])@rotation([0,1,0],rpy[1])@rotation([1,0,0],rpy[0])
    return m


def camera_model(cfg):
    camera = cfg['camera']
    k = array(camera['matrix'], (3,3), '相机内参')
    d = array(camera['distortion'], (5,), '畸变系数')
    if k[0,0]<=0 or k[1,1]<=0 or not np.allclose(k[2],[0,0,1]):
        raise ValueError('相机内参无效')
    size = camera['image_size']
    if not isinstance(size,list) or len(size)!=2 or any(type(x)is not int or x<=0 for x in size):
        raise ValueError('相机分辨率无效')
    return k,d


def camera_pose(cfg):
    k,d = camera_model(cfg)
    pixels = array(cfg['marks']['pixels'], (4,2), '四个标记像素')
    size=np.asarray(cfg['camera']['image_size'])
    if np.any(pixels<0) or np.any(pixels>=size):raise ValueError('标记像素超出图像')
    xy = array(cfg['marks']['xy_mm'], (4,2), '四个标记底座坐标')
    if not arm.number(cfg['table_z_mm']):
        raise ValueError('请测量桌面高度')
    if not cv2.isContourConvex(xy.astype(np.float32)) or abs(cv2.contourArea(xy.astype(np.float32)))<1:
        raise ValueError('四个标记须按外围顺序排列，不能共线或交叉')
    xyz = np.column_stack([xy,np.full(4,cfg['table_z_mm'])])
    ok,rvec,tvec = cv2.solvePnP(xyz,pixels,k,d,flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError('相机外参求解失败')
    r = cv2.Rodrigues(rvec)[0]
    if np.any((r@xyz.T+tvec).T[:,2]<=0):
        raise ValueError('标记位于相机后方，标定无效')
    return k,d,r,tvec.reshape(3)


def pixel_to_base(cfg, pixel, z):
    k,d,r,t = camera_pose(cfg)
    ray_camera = np.r_[cv2.undistortPoints(array([pixel],(1,2),'像素').reshape(1,1,2),k,d).reshape(2),1]
    origin = -r.T@t
    ray = r.T@ray_camera
    if abs(ray[2])<1e-8:
        raise ValueError('视线与目标平面平行')
    distance = (z-origin[2])/ray[2]
    if distance<=0:
        raise ValueError('目标平面不在相机前方')
    return origin+distance*ray


def calibration_report(cfg):
    _,_,r,t = camera_pose(cfg)
    block = array(cfg['block_size_mm'],(3,),'木块长宽高')
    if np.any(block<=0):raise ValueError('木块尺寸必须为正数')
    jaw = positive(cfg['jaw_open_width_mm'],'夹爪张开净宽')
    # 首版方块，不保证非方形物体的抓取方向。
    if abs(block[0]-block[1])>1:
        raise ValueError('首版仅处理顶面为正方形的木块')
    margin = (jaw-float(np.linalg.norm(block[:2])))/2
    limit = positive(cfg['max_position_error_mm'],'最大定位误差')
    if margin<=0 or limit*6>=margin:
        raise ValueError('定位误差阈值的六倍须小于夹爪单侧余量（预留模型、检测和执行误差）')
    cp=cfg['check_point']
    expected=array(cp['xy_mm'],(2,),'独立检查点')
    if not inside_workspace(cfg,expected) or np.min(np.linalg.norm(array(cfg['marks']['xy_mm'],(4,2),'标记')-expected,axis=1))<1:
        raise ValueError('独立检查点须位于工作区内，且不能使用标定角点')
    error=float(np.linalg.norm(pixel_to_base(cfg,cp['pixel'],cfg['table_z_mm'])[:2]-expected))
    # 顶面检查不能被桌面的四点拟合结果代替。
    checks=cfg['height_checks']
    if not isinstance(checks,list) or len(checks)<3:
        raise ValueError('请在至少三个不同位置采集木块顶面中心检查点')
    positions=[]; height_errors=[]
    for item in checks:
        xy=array(item['xy_mm'],(2,),'顶面检查底座坐标')
        positions.append(xy)
        actual=pixel_to_base(cfg,item['pixel'],cfg['table_z_mm']+block[2])[:2]
        height_errors.append(float(np.linalg.norm(actual-xy)))
    if np.linalg.matrix_rank(np.asarray(positions[1:])-positions[0])<2:
        raise ValueError('顶面检查位置须分散且不共线')
    if max([error]+height_errors)>limit:
        raise ValueError('独立检查定位误差超标，请重新标定')
    return dict(table_error_mm=error,height_errors_mm=height_errors,limit_mm=limit,
                jaw_margin_mm=margin,camera_in_base_mm=(-r.T@t).tolist())


def inside_workspace(cfg, xy, inset=0):
    polygon=array(cfg['marks']['xy_mm'],(4,2),'工作区').astype(np.float32)
    return cv2.pointPolygonTest(polygon,tuple(map(float,xy)),True)>=inset


def find_top(frame, cfg, vision):
    """只接受可验证的黄色顶面四边形；不把整块轮廓质心当作顶面。"""
    size=cfg['camera']['image_size']
    if [frame.shape[1],frame.shape[0]]!=size:raise ValueError('分辨率改变，请重新标定')
    low,high=vision['colors']['yellow']
    yellow=cv2.inRange(cv2.cvtColor(frame,cv2.COLOR_BGR2HSV),np.asarray(low),np.asarray(high))
    roi=vision['roi']
    if roi is None:raise ValueError('请设置工作区图像范围')
    keep=np.zeros_like(yellow);keep[roi[1]:roi[3],roi[0]:roi[2]]=255;yellow=cv2.bitwise_and(yellow,keep)
    outer=cv2.findContours(yellow,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[-2]
    outer=[c for c in outer if cv2.contourArea(c)>=vision['min_area']]
    if len(outer)!=1:raise ValueError('必须恰好一个黄色木块')
    edges=cv2.Canny(frame,60,160)
    contours=list(cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[-2])+outer
    block=array(cfg['block_size_mm'],(3,),'木块尺寸')
    limit=cfg['max_position_error_mm'];z=cfg['table_z_mm']+block[2]
    matches=[]
    for contour in contours:
        quad=cv2.approxPolyDP(contour,.02*cv2.arcLength(contour,True),True)
        if len(quad)!=4 or not cv2.isContourConvex(quad) or cv2.contourArea(quad)<vision['min_area']*.3:continue
        points=quad.reshape(4,2)
        mask=np.zeros_like(yellow);cv2.fillConvexPoly(mask,points,255)
        if cv2.countNonZero(cv2.bitwise_and(mask,yellow))/max(1,cv2.countNonZero(mask))<.9:continue
        world=np.asarray([pixel_to_base(cfg,p.tolist(),z)[:2] for p in points])
        lengths=np.linalg.norm(np.roll(world,-1,axis=0)-world,axis=1)
        if np.max(abs(lengths-block[0]))>limit*2:continue
        diagonals=[world[2]-world[0],world[3]-world[1]]
        if abs(float(diagonals[0]@diagonals[1]))>block[0]*limit*4:continue
        center=world.mean(axis=0)
        if not inside_workspace(cfg,center,float(np.linalg.norm(block[:2])/2)+limit):continue
        if not any(np.linalg.norm(center-m['xyz_mm'][:2])<limit for m in matches):
            matches.append(dict(xyz_mm=[float(center[0]),float(center[1]),float(z)],corners=points.tolist()))
    if len(matches)!=1:raise ValueError('无法唯一确定可信顶面中心；请改善光照与顶面边缘')
    return matches[0]


class Robot:
    def __init__(self, setup, arm_config, model_path=MODEL):
        self.cfg,self.arm=setup,arm_config
        data=Path(model_path).read_bytes();self.digest=hashlib.sha256(data).hexdigest()
        if setup['model']['urdf_sha256']!=self.digest:raise ValueError('模型版本不匹配')
        self.offset=array(setup['model']['servo_zero_deg'],(5,),'舵机零点')
        self.sign=array(setup['model']['servo_sign'],(5,),'舵机方向')
        if not np.all(np.isin(self.sign,[-1,1])):raise ValueError('关节方向必须为-1或1')
        self.tool=transform(array(setup['model']['tcp_xyz_mm'],(3,),'夹爪中心偏移')/1000,
                            np.radians(array(setup['model']['tcp_rpy_deg'],(3,),'夹爪中心方向')))
        xml=ET.fromstring(data);self.joints=[]
        for i in range(1,6):
            j=xml.find(f"joint[@name='joint{i}']")
            origin=j.find('origin');limit=j.find('limit')
            vector=lambda s:np.asarray([float(v) for v in s.split()])
            self.joints.append((transform(vector(origin.get('xyz')),vector(origin.get('rpy'))),
                                vector(j.find('axis').get('xyz')),float(limit.get('lower')),float(limit.get('upper'))))
        self.radii=array(setup['model']['collision_radii_mm'],(5,),'连杆碰撞包络半径')
        if np.any(self.radii<=0):raise ValueError('碰撞半径必须来自实际核对')
        self.tool_radius=positive(setup['model']['tool_radius_mm'],'末端含摄像头包络半径')
        boxes=setup['model']['tool_boxes_mm']
        if not isinstance(boxes,list) or not boxes:raise ValueError('请填写含摄像头、夹爪及开合扫掠的末端包络盒')
        self.boxes=[]
        for box in boxes:
            center=array(box['center'],(3,),'末端包络盒中心')
            half=array(box['half_size'],(3,),'末端包络盒半尺寸')
            if np.any(half<=0):raise ValueError('包络盒半尺寸必须为正')
            self.boxes.append((center,half))

    def fk(self, values, strict=True, enforce_model=True):
        q=array(values,(5,),'关节角度')
        if strict:
            for i,v in enumerate(q,1):arm.angle(self.arm,i,float(v))
        matrix=np.eye(4);points=[np.array([0,0,.064])*1000]
        for i,(origin,axis,low,high) in enumerate(self.joints):
            radians=math.radians((q[i]-self.offset[i])*self.sign[i])
            if enforce_model and not low<=radians<=high:raise ValueError('角度超出模型关节范围')
            joint=np.eye(4);joint[:3,:3]=rotation(axis,radians)
            matrix=matrix@origin@joint
            points.append(matrix[:3,3]*1000)
        matrix=matrix@self.tool;points.append(matrix[:3,3]*1000)
        return matrix,np.asarray(points)

    def ik(self, xyz, seed):
        target=array(xyz,(3,),'目标坐标')/1000
        q=array(seed,(5,),'初始解').copy()
        bounds=np.asarray([self.arm['joint_limits'][str(i)] for i in range(1,6)])
        desired_axis=np.array([0.,0.,-1.])
        if self.cfg.get('grasp_reference_angles') is not None:
            reference,_=self.fk(self.cfg['grasp_reference_angles'])
            origin_angle=math.atan2(reference[1,3],reference[0,3])
            target_angle=math.atan2(target[1],target[0])
            desired_axis=rotation([0,0,1],target_angle-origin_angle)@reference[:3,2]
            if desired_axis[2]>=0:raise ValueError('已验证的抓取方向未朝向桌面')
        def residual(v):
            m,_=self.fk(v,False)
            return np.r_[m[:3,3]-target,.06*(m[:3,2]-desired_axis)]
        for _ in range(250):
            e=residual(q)
            if np.linalg.norm(e[:3])<.00002 and np.linalg.norm(e[3:])<.00005:break
            jac=np.column_stack([(residual(q+np.eye(5)[i]*.001)-e)/.001 for i in range(5)])
            delta=-jac.T@np.linalg.solve(jac@jac.T+np.eye(6)*1e-7,e)
            accepted=False
            for scale in [1,.5,.25,.125,.0625,.03125]:
                proposal=q+delta*scale
                if np.any(proposal<bounds[:,0]) or np.any(proposal>bounds[:,1]):continue
                try:new=residual(proposal)
                except ValueError:continue
                if np.linalg.norm(new)<np.linalg.norm(e):q=proposal;accepted=True;break
            if not accepted:raise ValueError('目标不可达或姿态不满足关节范围')
        if np.any(q<bounds[:,0]) or np.any(q>bounds[:,1]):raise ValueError('反解越界，不截断')
        # 显式评估邻近整数格点；不截断连续越界解，不跳过目标校验。
        choices=[sorted({math.floor(v),math.ceil(v)}) for v in q]
        candidates=[]
        for integer in itertools.product(*choices):
            try:m,_=self.fk(list(integer))
            except ValueError:continue
            error=np.linalg.norm(m[:3,3]-target)*1000
            direction=np.linalg.norm(m[:3,2]-desired_axis)
            if error<=self.cfg['max_position_error_mm'] and direction<=.035:
                candidates.append((float(error+60*direction),list(integer)))
        if not candidates:
            error=residual(q)
            raise ValueError(f'反解整数化误差超标或接近倾角不匹配；连续位置误差{np.linalg.norm(error[:3])*1000:.3f}mm，轴误差{np.linalg.norm(error[3:])/.06:.4f}')
        return min(candidates)[1]

    def collision(self, values, carried=None, margin=0):
        m,points=self.fk(values,False)
        # joint1轴安装区固定于底座，检查其上的连杆及末端包络。
        segments=[(points[i+1],points[i+2],self.radii[i]) for i in range(4)]
        segments.append((points[5],points[5],max(self.tool_radius,self.radii[4])))
        table=self.cfg['table_z_mm']
        if not inside_workspace(self.cfg,m[:2,3]*1000,self.cfg['max_position_error_mm']):
            raise ValueError('末端扫掠超出已标定工作区')
        for a,b,radius in segments:
            if min(a[2],b[2])-radius-margin<=table:raise ValueError('连杆或末端包络接近桌面')
        for i,(a,b,ra) in enumerate(segments):
            for j,(c,d,rb) in enumerate(segments):
                if j<=i+1:continue
                if segment_distance(a,b,c,d)<=ra+rb+2*margin:raise ValueError('非相邻连杆碰撞包络相交')
        wrist=m@np.linalg.inv(self.tool)
        for center,half in self.boxes:
            local=np.array([[x,y,z] for x in [-half[0],half[0]] for y in [-half[1],half[1]] for z in [-half[2],half[2]]])+center
            world=local@wrist[:3,:3].T+wrist[:3,3]*1000
            if min(world[:,2])-margin<=table:raise ValueError('夹爪或摄像头包络接近桌面')
            for a,b,radius in segments[:3]:
                aa=wrist[:3,:3].T@(a-wrist[:3,3]*1000)-center
                bb=wrist[:3,:3].T@(b-wrist[:3,3]*1000)-center
                if segment_box_distance(aa,bb,half)<=radius+margin:raise ValueError('末端组件与连杆包络相交')
        if carried is not None:
            pose=m@carried
            block=array(self.cfg['block_size_mm'],(3,),'木块尺寸')/1000
            vertices=np.array([[x,y,z,1] for x in [-block[0]/2,block[0]/2]
                               for y in [-block[1]/2,block[1]/2] for z in [-block[2]/2,block[2]/2]])
            world=(pose@vertices.T).T[:,:3]*1000
            if min(world[:,2])<table-.001:raise ValueError('携带木块可能碰撞桌面')
            if any(not inside_workspace(self.cfg,vertex[:2],self.cfg['max_position_error_mm']) for vertex in world):
                raise ValueError('携带木块的扫掠超出已标定工作区')
            center=pose[:3,3]*1000;radius=np.linalg.norm(block)*500
            for a,b,r in segments[:-1]:
                if segment_distance(a,b,center,center)<=radius+r+margin:raise ValueError('携带木块可能碰撞连杆')
        return m

    def transition(self, start, end, carried=None, *, depth_first=False):
        # 校验实际单舵机顺序及其中间扫掠；不假定write会同步运动。
        q=np.asarray(start,float)
        reach=sum(np.linalg.norm(x[0][:3,3])*1000 for x in self.joints)+np.linalg.norm(self.tool[:3,3])*1000
        margin=reach*math.radians(1.25)
        self.collision(q.tolist(),carried,margin)
        for i,target in enumerate(end):
            arm.angle(self.arm,i+1,target)
        changed=[i for i in range(5) if end[i]!=q[i]]
        seen=set();failure='无符合包络的单关节转移顺序'
        goal=(1<<len(changed))-1
        def successors(mask,commands):
            nonlocal failure
            state=q.copy()
            for bit,joint in enumerate(changed):
                if mask&(1<<bit):state[joint]=end[joint]
            for bit,joint in enumerate(changed):
                next_mask=mask|(1<<bit)
                if mask&(1<<bit) or next_mask in seen:continue
                target=end[joint]
                try:
                    if carried is not None:self.sweep_block_floor(state,joint,target,carried)
                    count=max(1,int(math.ceil(abs(target-state[joint])/.25)))
                    if getattr(self,'_batch_sweeps',False):
                        trials=np.repeat(state[None],count+1,axis=0);trials[:,joint]=np.linspace(state[joint],target,count+1)
                        self.collision_samples(trials,carried,margin)
                    else:
                        for value in np.linspace(state[joint],target,count+1):
                            trial=state.copy();trial[joint]=value;self.collision(trial.tolist(),carried,margin)
                except ValueError as exc:failure=str(exc);continue
                yield next_mask,commands+[[joint+1,target]]
        if depth_first:
            # Every route changes each joint once, so every complete route has
            # the same number of commands. Explore a route lazily before its
            # siblings; failed masks are safe to memoize because pose and
            # carried transform depend only on the mask, not its history.
            def visit(mask,commands):
                if mask==goal:return commands
                for next_mask,next_commands in successors(mask,commands):
                    result=visit(next_mask,next_commands)
                    if result is not None:return result
                seen.add(mask)
                return None
            result=visit(0,[])
            if result is not None:return result
        else:
            queue=deque([(0,[])]);seen.add(0)
            while queue:
                mask,commands=queue.popleft()
                if mask==goal:return commands
                for next_mask,next_commands in successors(mask,commands):
                    seen.add(next_mask);queue.append((next_mask,next_commands))
        raise ValueError('无法规划本段单关节路径：'+failure)

    def sweep_block_floor(self, q, joint, target, carried):
        block=array(self.cfg['block_size_mm'],(3,),'木块尺寸')/1000
        vertices=np.array([[x,y,z,1] for x in [-block[0]/2,block[0]/2]
                           for y in [-block[1]/2,block[1]/2] for z in [-block[2]/2,block[2]/2]])
        heights=[]
        for delta in [0,90,180]:
            trial=q.copy();trial[joint]+=delta
            # 仅求解析系数，虚拟角度不作为目标；实际整段另行严格校验。
            matrix,_=self.fk(trial.tolist(),False,False)
            heights.append((matrix@carried@vertices.T)[2]*1000)
        a=(heights[0]-heights[2])/2;c=(heights[0]+heights[2])/2;b=heights[1]-c
        lo,hi=sorted([0,math.radians(target-q[joint])])
        for aa,bb,cc in zip(a,b,c):
            samples=[lo,hi]
            minimum=math.atan2(bb,aa)+math.pi
            samples.extend(minimum+2*math.pi*k for k in range(-2,3) if lo<=minimum+2*math.pi*k<=hi)
            if min(aa*math.cos(t)+bb*math.sin(t)+cc for t in samples)<self.cfg['table_z_mm']-.001:
                raise ValueError('木块的连续扫掠路径可能穿过桌面')


def segment_distance(a,b,c,d):
    # 求线段最近距离（包括退化点），不以采样替代自碰撞距离。
    u,v,w=b-a,d-c,a-c
    aa,bb,cc,dd,ee=float(u@u),float(u@v),float(v@v),float(u@w),float(v@w)
    candidates=[]
    if aa>1e-12 and cc>1e-12 and aa*cc-bb*bb>1e-12:
        s=(bb*ee-cc*dd)/(aa*cc-bb*bb);t=(aa*ee-bb*dd)/(aa*cc-bb*bb)
        if 0<=s<=1 and 0<=t<=1:candidates.append(np.linalg.norm(w+s*u-t*v))
    for point,p,q in [(a,c,d),(b,c,d),(c,a,b),(d,a,b)]:
        line=q-p;length=float(line@line)
        t=max(0,min(1,float((point-p)@line)/length)) if length>1e-12 else 0
        candidates.append(np.linalg.norm(point-(p+t*line)))
    return float(min(candidates))


def segment_box_distance(a,b,half):
    direction=b-a;cuts=[0.,1.]
    for i in range(3):
        if abs(direction[i])>1e-12:
            for face in [-half[i],half[i]]:
                t=(face-a[i])/direction[i]
                if 0<t<1:cuts.append(float(t))
    cuts=sorted(cuts);candidates=list(cuts)
    for lo,hi in zip(cuts,cuts[1:]):
        midpoint=a+direction*(lo+hi)/2
        active=abs(midpoint)>half
        offset=a-np.sign(midpoint)*half
        denominator=float(direction[active]@direction[active])
        if denominator>1e-12:
            t=-float(offset[active]@direction[active])/denominator
            if lo<=t<=hi:candidates.append(t)
    return float(min(np.linalg.norm(np.maximum(abs(a+direction*t)-half,0)) for t in candidates))
