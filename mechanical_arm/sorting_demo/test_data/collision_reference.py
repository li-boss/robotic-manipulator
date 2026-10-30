"""Pre-vectorization collision reference for equivalence tests; no hardware."""
import numpy as np
from cad_geometry import obb_overlap, array

def collision(robot, values, carried=None, margin=0):
    """CAD模式以分解的网格盒覆盖模型，包含相机和夹爪全开候选包络。"""
    cfg = robot.cfg
    m, _ = robot.fk(values, False)
    matrices = [np.asarray(v) for v in robot.matrices(values)]
    shapes = []
    for i, boxes in enumerate(cfg['cad']['collision_boxes']):
        pose = matrices[i]
        for box in boxes:
            c = pose[:3, :3] @ np.asarray(box['center_mm']) + pose[:3, 3] * 1000
            h = np.asarray(box['half_size_mm'])
            shapes.append((i, (c, pose[:3, :3], h)))
    bounds = robot.bounds
    for i, (center, r, half) in shapes:
        extent = abs(r) @ half
        if np.any(center - extent - margin < bounds[0]) or np.any(center + extent + margin > bounds[1]):
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
    centers=np.asarray([s[0] for _,s in shapes]);extents=np.asarray([abs(s[1])@s[2] for _,s in shapes])
    links=np.asarray([i for i,_ in shapes])
    reach=sum(np.linalg.norm(j[0][:3,3])*1000 for j in robot.joints)+np.linalg.norm(robot.tool[:3,3])*1000
    distances=np.linalg.norm(centers[:,None]-np.asarray([p[:3,3]*1000 for p in matrices[1:]])[None,:],axis=2)
    radii=np.asarray([np.linalg.norm(s[2]) for _,s in shapes])
    local_margin=np.sum((distances+radii[:,None])*(np.arange(1,6)[None,:]<=links[:,None]),axis=1)*(margin/reach)
    base_pair=(links[:,None]==0)|(links[None,:]==0)
    padding=np.where(base_pair,local_margin[:,None]+local_margin[None,:],2*margin)
    broad=np.all(abs(centers[:,None]-centers[None,:])<=extents[:,None]+extents[None,:]+padding[:,:,None],axis=2)
    pairs=np.triu(broad&(abs(links[:,None]-links[None,:])>1),1)
    for left,right in zip(*np.nonzero(pairs)):
        # 底座固定，使用该盒至各上游关节的距离界，而不是整臂末端位移。
        pair_margin=padding[left,right]/2
        if obb_overlap(shapes[left][1],shapes[right][1],pair_margin):
            raise ValueError('CAD非相邻连杆、相机或夹爪包络相交')
    if carried is not None:
        pose = m @ carried
        block = (pose[:3, 3] * 1000, pose[:3, :3], np.asarray(cfg['block_size_mm']) / 2)
        extent = abs(block[1]) @ block[2]
        if block[0][2] - extent[2] < cfg['table_z_mm'] - .001:
            raise ValueError('携带方块接触或穿过桌面')
        if np.any(block[0] - extent - margin < bounds[0]) or np.any(block[0] + extent + margin > bounds[1]):
            raise ValueError('携带方块超出允许空间')
        for i, shape in shapes:
            if i < 5 and obb_overlap(block, shape, margin):
                raise ValueError('携带方块接近机械臂或相机')
    else:
        block = None
    for obstacle in cfg['obstacles']:
        other = (array(obstacle['center_mm'], (3,), '障碍中心'), np.eye(3),
                 array(obstacle['half_size_mm'], (3,), '障碍半尺寸'))
        if np.any(other[2] <= 0):
            raise ValueError('障碍尺寸须为正')
        if any(obb_overlap(shape, other, margin) for _, shape in shapes) or (block is not None and obb_overlap(block, other, margin)):
            raise ValueError('模型或携带方块接近已登记障碍')
    return m
