"""自动抓放配置与有限计划，独立于ROS和硬件。所有长度均为毫米。"""
import copy
import hashlib
from pathlib import Path

import numpy as np
from core import arm
from auto_geometry import MODEL, Robot, array, calibration_report, inside_workspace, positive


def default_setup():
    return dict(version=1,model_reviewed=False,calibration_reviewed=False,path_reviewed=False,
        arm_config_sha256=None,
        camera=dict(matrix=None,distortion=None,image_size=[640,480],samples=[],anchor_frame=None),
        marks=dict(pixels=None,xy_mm=None),check_point=dict(pixel=None,xy_mm=None),height_checks=[],
        observation_angles=None,observation_readings=None,grasp_reference_angles=None,table_z_mm=None,block_size_mm=None,
        jaw_open_width_mm=None,max_position_error_mm=None,grasp_height_mm=None,
        transfer_height_mm=None,release_gap_mm=None,yellow_xy_mm=None,
        model=dict(urdf_sha256=hashlib.sha256(MODEL.read_bytes()).hexdigest(),servo_zero_deg=[90]*5,
                   servo_sign=[1]*5,tcp_xyz_mm=None,tcp_rpy_deg=None,collision_radii_mm=None,
                   tool_radius_mm=None,tool_boxes_mm=None,measurements=[]))


def model_report(cfg, arms):
    robot=Robot(cfg,arms)
    samples=cfg['model']['measurements']
    if not isinstance(samples,list) or len(samples)<3:raise ValueError('请核对至少三个不同姿态的夹爪中心位置')
    errors=[]; poses=[]
    for sample in samples:
        q=sample['angles'];poses.append(q)
        matrix,_=robot.fk(q)
        error=float(np.linalg.norm(matrix[:3,3]*1000-array(sample['xyz_mm'],(3,),'实测夹爪中心')))
        if error>cfg['max_position_error_mm']:raise ValueError('模型正解与实测位置误差超标')
        errors.append(error)
    if len({tuple(x) for x in poses})<3:raise ValueError('模型核对姿态不能重复')
    return dict(urdf_sha256=robot.digest,fk_errors_mm=errors)


def readiness(cfg, arms):
    issues=[];reports={}
    for name,check in [('calibration',lambda:calibration_report(cfg)),('model',lambda:model_report(cfg,arms)),
                       ('parameters',lambda:parameters(cfg,arms))]:
        try:reports[name]=check()
        except (ValueError,TypeError,KeyError,IndexError) as exc:issues.append(dict(item=name,reason=str(exc)))
    for field,label in [('model_reviewed','模型与尺寸核对'),('calibration_reviewed','标定核对'),('path_reviewed','现场空载路径验证')]:
        if cfg.get(field) is not True:issues.append(dict(item=field,reason='尚未完成：'+label))
    if cfg.get('arm_config_sha256')!=arm_fingerprint(arms):
        issues.append(dict(item='arm_config_sha256',reason='机械臂配置尚未绑定核对，或核对后已改变'))
    return dict(ready=not issues,issues=issues,reports=reports)


def arm_fingerprint(arms):
    import json
    return hashlib.sha256(json.dumps(arms,sort_keys=True,allow_nan=False).encode()).hexdigest()


def parameters(cfg, arms):
    observation=array(cfg['observation_angles'],(5,),'观察目标角度').tolist()
    for i,value in enumerate(observation,1):arm.angle(arms,i,value)
    array(cfg['observation_readings'],(5,),'标定时原始观察读数')
    reference=array(cfg['grasp_reference_angles'],(5,),'一次已验证抓取的关节目标')
    for i,value in enumerate(reference,1):arm.angle(arms,i,float(value))
    for i,value in enumerate(cfg['observation_readings'],1):arm.observed_angle(arms,i,value)
    if np.max(abs(np.asarray(cfg['observation_readings'])-observation))>1:
        raise ValueError('标定观察读数与明确目标偏差超过1°，请检查姿态与模型')
    arm.timing(arms)
    for key in ['open','closed']:arm.angle(arms,6,arms.get('gripper',{}).get(key))
    if arms['gripper']['open']==arms['gripper']['closed']:raise ValueError('开合角度必须来自不同的实测状态')
    height=positive(cfg['grasp_height_mm'],'抓取中心离桌高度')
    block=array(cfg['block_size_mm'],(3,),'木块尺寸')
    if height>=block[2]:raise ValueError('抓取中心必须低于木块顶面')
    transfer=positive(cfg['transfer_height_mm'],'转移中心离桌高度')
    if transfer<=block[2]+height:raise ValueError('转移中心高度不足以使木块离开桌面')
    gap=positive(cfg['release_gap_mm'],'放置释放间隙')
    dest=array(cfg['yellow_xy_mm'],(2,),'黄色放置中心')
    if not inside_workspace(cfg,dest,np.linalg.norm(block[:2])/2+cfg['max_position_error_mm']):
        raise ValueError('放置位置及木块外形必须在已标定区域内')
    return dict(observation=observation,grasp_height_mm=height,transfer_height_mm=transfer)


def build_plan(cfg, arms, xyz, require_path=True):
    if cfg.get('arm_config_sha256')!=arm_fingerprint(arms):raise ValueError('机械臂配置改变，请重新核对标定与路径')
    for field in ['model_reviewed','calibration_reviewed']:
        if cfg.get(field) is not True:raise ValueError('请先完成模型核对与标定')
    if require_path and cfg.get('path_reviewed') is not True:raise ValueError('先逐段验证空载路径，再启用完整抓放')
    calibration_report(cfg);model_report(cfg,arms);parameters(cfg,arms)
    xyz=array(xyz,(3,),'木块顶面中心')
    block=array(cfg['block_size_mm'],(3,),'木块尺寸')
    if abs(xyz[2]-cfg['table_z_mm']-block[2])>.001:raise ValueError('目标高度不匹配')
    if not inside_workspace(cfg,xyz[:2],np.linalg.norm(block[:2])/2+cfg['max_position_error_mm']):
        raise ValueError('目标或木块边缘超出工作区')
    robot=Robot(cfg,arms);current=[int(x) for x in cfg['observation_angles']]
    duration,settle=arm.timing(arms);steps=[];attachment=None
    def grip(label,value):steps.append(dict(label=label,joints=[[6,arm.angle(arms,6,value)]],motion_ms=duration,settle_ms=settle))
    def move(label,point=None,q=None):
        nonlocal current
        target=q if q is not None else robot.ik(point,current)
        commands=robot.transition(current,target,attachment)
        # 每条指令等待完成，执行顺序和几何检查顺序一致。
        for joint,angle in commands:
            steps.append(dict(label=label,joints=[[joint,angle]],motion_ms=duration,settle_ms=settle))
        current=list(target)
    z=cfg['table_z_mm'];pickup=[*xyz[:2],z+cfg['grasp_height_mm']]
    above=[*xyz[:2],z+cfg['transfer_height_mm']]
    dest=cfg['yellow_xy_mm'];drop=[*dest,z+cfg['grasp_height_mm']+cfg['release_gap_mm']]
    dest_above=[*dest,z+cfg['transfer_height_mm']]
    grip('张开夹爪',arms['gripper']['open'])
    move('到达取物上方',above);move('下降抓取',pickup)
    grip('夹持木块',arms['gripper']['closed'])
    matrix,_=robot.fk(current);block_pose=np.eye(4);block_pose[:3,3]=[*(xyz[:2]/1000),(z+block[2]/2)/1000]
    attachment=np.linalg.inv(matrix)@block_pose
    move('抬升木块',above);move('转移至黄色区域上方',dest_above);move('下降放置',drop)
    grip('释放木块',arms['gripper']['open']);attachment=None
    move('离开放置点',dest_above);move('返回观察姿态',q=cfg['observation_angles'])
    if not steps or len(steps)>100:raise ValueError('计划必须是最多100条的有限动作')
    for step in steps:
        for joint,value in step['joints']:arm.angle(arms,joint,value)
        arm.timing(dict(arms,motion_ms=step['motion_ms'],settle_ms=step['settle_ms']))
    return dict(target_xyz_mm=xyz.tolist(),destination_xy_mm=dest,steps=steps,
                model_sha256=robot.digest,duration_ms=sum(s['motion_ms']+s['settle_ms'] for s in steps),
                message='完整有限计划；逐个舵机等待完成；结果需要人工观察确认')
