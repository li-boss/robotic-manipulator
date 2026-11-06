"""Chunked CAD sweep checks, equivalent to scalar checks; no hardware access."""
import math
import numpy as np

from manipulation_ik import batch_matrices


_EPS=1e-7  # Ambiguous floating-point boundaries are resolved by the scalar code.


def overlaps(ca,ra,ha,cb,rb,hb,margin):
    delta=cb-ca
    axes=np.concatenate((ra.transpose(0,2,1),rb.transpose(0,2,1),
        np.cross(ra.transpose(0,2,1)[:,:,None,:],rb.transpose(0,2,1)[:,None,:,:]).reshape(-1,9,3)),axis=1)
    lengths=np.linalg.norm(axes,axis=2);valid=lengths>=1e-9
    unit=np.divide(axes,lengths[:,:,None],out=np.zeros_like(axes),where=valid[:,:,None])
    radius=np.sum(abs(np.einsum('nai,nij->naj',unit,ra))*ha[:,None,:],axis=2)
    radius+=np.sum(abs(np.einsum('nai,nij->naj',unit,rb))*hb[:,None,:],axis=2)+2*np.asarray(margin)[:,None]
    gaps=abs(np.einsum('nai,ni->na',unit,delta))-radius
    separation=np.max(np.where(valid,gaps,-np.inf),axis=1)
    return separation<=0,abs(separation)<_EPS


def check_chunk(robot,values,carried,margin):
    import cad_geometry
    cfg=robot.cfg;qs=np.asarray(values,float);count=len(qs)
    if qs.ndim!=2 or qs.shape[1]!=5 or not np.isfinite(qs).all():raise ValueError('扫掠样本必须是有限五关节数组')
    if not math.isfinite(margin) or margin<0:raise ValueError('扫掠包络余量无效')
    if carried is not None and (np.shape(carried)!=(4,4) or not np.isfinite(carried).all()):raise ValueError('携带变换无效')
    angles=np.radians((qs-robot.offset)*robot.sign)
    tolerance=math.radians(1) if cad_geometry.dynamic_capture(cfg) else 0
    if np.any(angles<np.array([j[2] for j in robot.joints])-tolerance) or np.any(angles>np.array([j[3] for j in robot.joints])+tolerance):
        # The scalar path produces the established exact range error.
        for q in qs:robot.fk(q.tolist(),False)
        raise ValueError('扫掠姿态超出模型范围')
    poses=batch_matrices(robot,qs);tcp=poses[:,-1]@robot.tool
    links=robot._cad_box_links;halves=robot._cad_box_halves
    selected=poses[:,links]
    rotations=selected[:,:,:3,:3]
    centers=np.einsum('nbij,bj->nbi',rotations,robot._cad_box_centers)+selected[:,:,:3,3]*1000
    extents=np.einsum('nbij,bj->nbi',abs(rotations),halves)
    below=centers-extents-margin-robot.bounds[0];above=robot.bounds[1]-centers-extents-margin
    bad=np.any(below<0,axis=(1,2))|np.any(above<0,axis=(1,2))
    ambiguous=np.any(abs(below)<_EPS,axis=(1,2))|np.any(abs(above)<_EPS,axis=(1,2))
    for i in range(2,6):
        pose=poses[:,i];points=robot._cad_floor_support[i]
        low=np.min(np.einsum('pj,nj->np',points,pose[:,2,:3]),axis=1)+pose[:,2,3]*1000
        if i==5:
            box=cfg['cad']['collision_boxes'][5][-1];c=np.asarray(box['center_mm']);h=np.asarray(box['half_size_mm'])
            jaw=pose[:,2,:3]@c+pose[:,2,3]*1000-abs(pose[:,2,:3])@h;low=np.minimum(low,jaw)
        gap=low-margin-cfg['table_z_mm'];bad|=gap<=0;ambiguous|=abs(gap)<_EPS
    distance=np.linalg.norm(centers[:,:,None,:]-poses[:,None,1:,:3,3]*1000,axis=3)
    local=np.sum((distance+robot._cad_box_radii[None,:,None])*(np.arange(1,6)[None,None,:]<=links[None,:,None]),axis=2)*(margin/robot._cad_reach)
    base_pair=(links[:,None]==0)|(links[None,:]==0)
    padding=np.where(base_pair[None],local[:,:,None]+local[:,None,:],2*margin)
    gaps=abs(centers[:,:,None]-centers[:,None,:])-extents[:,:,None]-extents[:,None,:]-padding[:,:,:,None]
    pair_mask=np.triu(abs(links[:,None]-links[None,:])>1,1)
    maximum=np.max(gaps,axis=3)
    ambiguous|=np.any((abs(maximum)<_EPS)&pair_mask[None],axis=(1,2))
    n,left,right=np.nonzero(np.all(gaps<=0,axis=3)&pair_mask[None])
    if len(n):
        hit,near=overlaps(centers[n,left],rotations[n,left],halves[left],centers[n,right],rotations[n,right],halves[right],padding[n,left,right]/2)
        bad[n[hit]]=True;ambiguous[n[near]]=True
    if carried is not None:
        block_pose=tcp@carried;block_center=block_pose[:,:3,3]*1000;block_rotation=block_pose[:,:3,:3]
        block_half=np.asarray(cfg['block_size_mm'])/2;block_extent=abs(block_rotation)@block_half
        floor=block_center[:,2]-block_extent[:,2]-cfg['table_z_mm']+.001
        bad|=floor<0;ambiguous|=abs(floor)<_EPS
        lo=block_center-block_extent-margin-robot.bounds[0];hi=robot.bounds[1]-block_center-block_extent-margin
        bad|=np.any(lo<0,axis=1)|np.any(hi<0,axis=1)
        ambiguous|=np.any(abs(lo)<_EPS,axis=1)|np.any(abs(hi)<_EPS,axis=1)
        gap=abs(block_center[:,None]-centers)-block_extent[:,None]-extents-2*margin
        eligible=links<5;maximum=np.max(gap,axis=2)
        ambiguous|=np.any((abs(maximum)<_EPS)&eligible[None],axis=1)
        n,box=np.nonzero(np.all(gap<=0,axis=2)&eligible[None])
        if len(n):
            hit,near=overlaps(block_center[n],block_rotation[n],np.repeat(block_half[None],len(n),axis=0),centers[n,box],rotations[n,box],halves[box],np.full(len(n),margin))
            bad[n[hit]]=True;ambiguous[n[near]]=True
    for obstacle in cfg['obstacles']:
        center=cad_geometry.array(obstacle['center_mm'],(3,),'障碍中心');half=cad_geometry.array(obstacle['half_size_mm'],(3,),'障碍半尺寸')
        if np.any(half<=0):raise ValueError('障碍尺寸须为正')
        gap=abs(centers-center)-extents-half-2*margin;maximum=np.max(gap,axis=2)
        ambiguous|=np.any(abs(maximum)<_EPS,axis=1)
        n,box=np.nonzero(np.all(gap<=0,axis=2))
        if len(n):
            hit,near=overlaps(centers[n,box],rotations[n,box],halves[box],np.repeat(center[None],len(n),axis=0),np.repeat(np.eye(3)[None],len(n),axis=0),np.repeat(half[None],len(n),axis=0),np.full(len(n),margin))
            bad[n[hit]]=True;ambiguous[n[near]]=True
        if carried is not None:
            gap=abs(block_center-center)-block_extent-half-2*margin;maximum=np.max(gap,axis=1)
            ambiguous|=abs(maximum)<_EPS
            n=np.flatnonzero(np.all(gap<=0,axis=1))
            if len(n):
                hit,near=overlaps(block_center[n],block_rotation[n],np.repeat(block_half[None],len(n),axis=0),np.repeat(center[None],len(n),axis=0),np.repeat(np.eye(3)[None],len(n),axis=0),np.repeat(half[None],len(n),axis=0),np.full(len(n),margin))
                bad[n[hit]]=True;ambiguous[n[near]]=True
    # Preserve the first sample's scalar failure reason and resolve near-contact
    # numerical cases using the unchanged reference implementation.
    for index in np.flatnonzero(bad|ambiguous):cad_geometry.collision(robot,qs[index].tolist(),carried,margin)


def check_samples(robot,values,carried=None,margin=0):
    for start in range(0,len(values),16):check_chunk(robot,values[start:start+16],carried,margin)
