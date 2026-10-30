"""无永久标记的经验映射；纯计算，不初始化硬件，不声称几何避障。"""
import hashlib
import json
import math
import cv2
import numpy as np
from core import arm

COMMON=['transfer','place_above','place']
ROUTES=['观察到转移','转移到取物上方','下降抓取','抬升木块','取物上方到转移',
        '转移到放置上方','下降放置','离开放置点','放置上方到转移','返回观察']


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def defaults():
    return dict(version=1,baseline=None,common={},orders={name:None for name in ROUTES},
                samples=[],fit=None,validations=[],arm_config_sha256=None,reference_verified=False)


def numbers(values,length,name):
    if not isinstance(values,list) or len(values)!=length or any(not arm.number(x) for x in values):
        raise ValueError(name+'需要'+str(length)+'个有限数值')
    return np.asarray(values,float)


def pose(arms,values):
    numbers(values,5,'明确关节目标')
    return [arm.angle(arms,i,value) for i,value in enumerate(values,1)]


def prerequisites(cfg,arms):
    if cfg['version']!=1 or cfg['arm_config_sha256']!=digest(arms):raise ValueError('机械臂配置未绑定或已改变，请重新准备')
    if not cfg['baseline']:raise ValueError('先保存空桌面观察姿态')
    pose(arms,cfg['baseline']['angles'])
    if cfg.get('task_mode')=='pickup_only':
        arm.timing(arms)
        for key in ['open','closed']:arm.angle(arms,6,arms.get('gripper',{}).get(key))
        if arms['gripper']['open']==arms['gripper']['closed']:raise ValueError('夹爪开合目标不能相同')
        return
    for name in COMMON:
        if name not in cfg['common']:raise ValueError('尚未记录：'+name)
        pose(arms,cfg['common'][name]['angles'])
    for name in ROUTES:
        order=cfg['orders'].get(name)
        if not isinstance(order,list) or len(order)!=5 or set(order)!=set(range(1,6)) or any(type(i)is not int for i in order):
            raise ValueError('请明确核对每阶段单关节顺序：'+name)
    arm.timing(arms)
    for key in ['open','closed']:arm.angle(arms,6,arms.get('gripper',{}).get(key))
    if arms['gripper']['open']==arms['gripper']['closed']:raise ValueError('夹爪开合目标不能相同')


def reported_mode(cfg):
    return cfg.get('mapping_mode') == 'reported_cards'


def mapping_samples(cfg):
    return cfg.get('reported_mapping', {}).get('samples', []) if reported_mode(cfg) else cfg['samples']


def mapping_fit(cfg):
    return cfg.get('reported_mapping', {}).get('fit') if reported_mode(cfg) else cfg['fit']


def mapping_source(cfg):
    if reported_mode(cfg):
        report = cfg.get('reported_mapping', {})
        return dict(task_mode=cfg.get('task_mode'), baseline=cfg['baseline'],
                    arm_config_sha256=cfg['arm_config_sha256'], mapping_mode='reported_cards',
                    source={key: value for key, value in report.items() if key != 'fit'})
    return dict(task_mode=cfg.get('task_mode','pick_place'),baseline=cfg['baseline'],common=cfg['common'],orders=cfg['orders'],
                samples=cfg['samples'],arm_config_sha256=cfg['arm_config_sha256'])


def fit(cfg,arms):
    prerequisites(cfg,arms)
    samples=mapping_samples(cfg)
    if reported_mode(cfg):
        report=cfg.get('reported_mapping',{})
        if cfg.get('task_mode')!='pickup_only' or report.get('source_type')!='user_report' or report.get('operator_report') is not True:
            raise ValueError('九点映射必须明确记录用户报告来源，且仅用于抓取保持')
        if len(samples)!=9:raise ValueError('九点卡片映射需要恰好9个位置')
    if len(samples)<9:raise ValueError('首轮至少需要9个已验证样本')
    points=np.asarray([numbers(x['pixel'],2,'样本像素') for x in samples])
    w,h=cfg['baseline']['image_size']
    if np.any(points<0) or np.any(points>=np.asarray([w,h])):raise ValueError('样本超出图像')
    if np.linalg.matrix_rank(points-points.mean(axis=0))<2:raise ValueError('样本共线，无法建立二维映射')
    for i,p in enumerate(points):
        if i and np.min(np.linalg.norm(points[:i]-p,axis=1))<5:raise ValueError('样本重复或过于接近')
    for sample in samples:
        pose(arms,sample['above']);pose(arms,sample['grasp'])
        if not reported_mode(cfg) and sample.get('verified') is not True:raise ValueError('样本未完成空载及带块验证')
    subdiv=cv2.Subdiv2D((0,0,w,h))
    for p in points:subdiv.insert(tuple(map(float,p)))
    cells=set()
    for tri in subdiv.getTriangleList():
        ids=[]
        for p in tri.reshape(3,2):
            ds=np.linalg.norm(points-p,axis=1);idx=int(np.argmin(ds))
            if ds[idx]>.01:break
            ids.append(idx)
        if len(ids)==3 and len(set(ids))==3:cells.add(tuple(sorted(ids)))
    if not cells:raise ValueError('样本不能构成有效覆盖单元')
    return dict(version=digest(mapping_source(cfg)),cells=[dict(id='-'.join(map(str,c)),indices=list(c)) for c in sorted(cells)],
                coverage=cv2.convexHull(points.astype(np.float32)).reshape(-1,2).tolist())


def locate(cfg,pixel):
    fitted=mapping_fit(cfg)
    if not fitted or fitted['version']!=digest(mapping_source(cfg)):raise ValueError('样本映射未拟合或已过期')
    point=numbers(pixel,2,'目标像素')
    for cell in fitted['cells']:
        vertices=np.asarray([mapping_samples(cfg)[i]['pixel'] for i in cell['indices']],float)
        weights=np.linalg.solve(np.vstack([vertices.T,np.ones(3)]),np.r_[point,1])
        if np.all(weights>=0) and np.all(weights<=1):return cell,weights
        # 仅允许解方程的舍入误差，不扩大覆盖区或裁剪目标。
        if np.all(weights>=-1e-10) and np.all(weights<=1+1e-10):return cell,weights
    raise ValueError('目标不在示教覆盖范围内，请在附近补样本')


def cell_ready(cfg,cell_id):
    if reported_mode(cfg):
        report=cfg.get('reported_mapping',{})
        fitted=mapping_fit(cfg)
        return bool(cfg.get('task_mode')=='pickup_only' and report.get('source_type')=='user_report'
                    and report.get('operator_report') is True and fitted
                    and fitted['version']==digest(mapping_source(cfg))
                    and any(c['id']==cell_id for c in fitted['cells']))
    if not cfg.get('reference_verified'):return False
    valid=[v for v in cfg['validations'] if v.get('passed') is True and v.get('fit_version')==cfg['fit']['version']]
    return len(valid)>=5 and any(v['cell_id']==cell_id for v in valid)


def interpolate(cfg,arms,pixel,require_validated=True):
    prerequisites(cfg,arms)
    cell,weights=locate(cfg,pixel)
    if require_validated and not cell_ready(cfg,cell['id']):raise ValueError('该覆盖单元或至少5个独立位置尚未验证')
    result={}
    for key in ['above','grasp']:
        values=np.asarray([mapping_samples(cfg)[i][key] for i in cell['indices']],float)
        # 用差分形式保持所有样本相同的整数目标精确不变（例如5号90°）。
        predicted=values[0]+weights[1:]@(values[1:]-values[0])
        # 连续预测值先检查范围，再明确转为整数；禁止以取整掩盖越界。
        for i,value in enumerate(predicted,1):
            limits=arms['joint_limits'][str(i)]
            hard=(90,270) if i==5 else (0,180)
            if not max(limits[0],hard[0])<=value<=min(limits[1],hard[1]):raise ValueError('连续预测目标越界')
        result[key]=pose(arms,[math.floor(float(v)+.5) for v in predicted])
    if cfg.get('task_mode')=='pickup_only':
        orders=[mapping_samples(cfg)[i].get('stage_orders') for i in cell['indices']]
        if not orders[0] or any(order!=orders[0] for order in orders):raise ValueError('此单元的示教顺序不一致，不能自动选择路径')
        result['stage_orders']=orders[0]
        times=[mapping_samples(cfg)[i].get('stage_motion_ms') for i in cell['indices']]
        if any(value!=times[0] for value in times):raise ValueError('此单元的示教用时不一致，请核对卡片参数')
        if times[0]:result['stage_motion_ms']=times[0]
    return dict(**result,cell_id=cell['id'],weights=weights.tolist())


def build(cfg,arms,pixel,pair=None,require_validated=True):
    prerequisites(cfg,arms)
    predicted=pair if pair is not None else interpolate(cfg,arms,pixel,require_validated)
    above=pose(arms,predicted['above']);grasp=pose(arms,predicted['grasp'])
    duration,settle=arm.timing(arms);steps=[];current=pose(arms,cfg['baseline']['angles'])
    if cfg.get('task_mode')=='pickup_only':
        orders=predicted.get('stage_orders')
        if not isinstance(orders,dict):raise ValueError('请保存上方和抓取按钮的六关节顺序')
        for stage,targets,gripper in [('above',above,'open'),('grasp',grasp,'closed')]:
            order=orders.get(stage)
            if not isinstance(order,list) or len(order)!=6 or set(order)!=set(range(1,7)) or any(type(j)is not int for j in order):
                raise ValueError('每个姿态的顺序须为1～6号各一次')
            # 接近过程中保持张开；夹持必须是最后一条，不能先闭合再下降。
            if stage=='grasp' and order[-1]!=6:raise ValueError('抓取顺序需最后执行6号夹爪，请调整卡片顺序')
            values=targets+[arm.angle(arms,6,arms['gripper'][gripper])]
            stage_duration,_=arm.timing(dict(arms,motion_ms=predicted.get('stage_motion_ms',{}).get(stage,duration)))
            for joint in order:
                steps.append(dict(label='取物上方' if stage=='above' else '抓取并保持',joints=[[joint,values[joint-1]]],motion_ms=stage_duration,settle_ms=settle))
        return dict(strategy='teaching',task_mode='pickup_only',mapping_mode=cfg.get('mapping_mode','measured'),pixel=list(map(float,pixel)),cell_id=predicted.get('cell_id'),steps=steps,
            duration_ms=sum(s['motion_ms']+s['settle_ms'] for s in steps),message='仅接近并夹持；结束保持抓取姿态，不抬升、不放置、不松爪')
    def grip(label,key):
        steps.append(dict(label=label,joints=[[6,arm.angle(arms,6,arms['gripper'][key])]],motion_ms=duration,settle_ms=settle))
    def move(label,targets):
        nonlocal current
        checked=pose(arms,targets)
        for joint in cfg['orders'][label]:
            if checked[joint-1]!=current[joint-1]:
                steps.append(dict(label=label,joints=[[joint,checked[joint-1]]],motion_ms=duration,settle_ms=settle))
        current=checked
    transfer=cfg['common']['transfer']['angles'];place_above=cfg['common']['place_above']['angles'];place=cfg['common']['place']['angles']
    grip('张开夹爪','open');move(ROUTES[0],transfer);move(ROUTES[1],above);move(ROUTES[2],grasp)
    grip('夹持木块','closed');move(ROUTES[3],above);move(ROUTES[4],transfer)
    move(ROUTES[5],place_above);move(ROUTES[6],place);grip('释放木块','open')
    move(ROUTES[7],place_above);move(ROUTES[8],transfer);move(ROUTES[9],cfg['baseline']['angles'])
    if not 0<len(steps)<=100:raise ValueError('计划必须为最多100条的有限动作')
    return dict(strategy='teaching',pixel=list(map(float,pixel)),cell_id=predicted.get('cell_id'),steps=steps,
        duration_ms=sum(s['motion_ms']+s['settle_ms'] for s in steps),
        message='经验映射；不声称精确几何碰撞检查；仅授权本次有限计划，结果由人工确认')


def readiness(cfg,arms):
    issues=[]
    try:prerequisites(cfg,arms)
    except (ValueError,KeyError,TypeError) as exc:issues.append(str(exc))
    fitted=mapping_fit(cfg);cells=[]
    if not fitted or fitted['version']!=digest(mapping_source(cfg)):issues.append('尚未完成9个样本并拟合')
    else:
        cells=[dict(**c,enabled=cell_ready(cfg,c['id'])) for c in fitted['cells']]
        if not any(c['enabled'] for c in cells):issues.append('尚无通过独立验证的覆盖单元')
    return dict(ready=not issues,issues=issues,cells=cells)
