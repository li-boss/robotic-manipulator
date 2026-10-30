"""Pure nine-point reference conversion and bounded interpolation. No device I/O."""
import copy
import hashlib
import json
import math
import numpy as np


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def build_reference(cfg,arms,teaching):
    from core import arm
    from manipulation_geometry import project_to_plane,calibration_version,array
    report=teaching.get('reported_mapping',{})
    if teaching.get('mapping_mode')!='reported_cards' or report.get('operator_report') is not True or report.get('source_type')!='user_report':
        raise ValueError('九点参考未建立或缺少用户报告来源')
    rows=report.get('samples',[]);baseline=teaching.get('baseline') or {}
    if len(rows)!=9 or baseline.get('image_size')!=cfg['camera']['image_size']:
        raise ValueError('九点数量或原始图像分辨率与当前标定不一致')
    angles=array(baseline.get('readings'),(5,),'九点旧观察真实读数').tolist()
    for i,q in enumerate(angles,1):arm.observed_angle(arms,i,q)
    height=cfg['table_z_mm']+cfg['block_size_mm'][2]
    samples=[]
    for row in rows:
        point=project_to_plane(cfg,row['pixel'],height,angles)
        item=dict(name=row['name'],slot=row['slot'],pixel=copy.deepcopy(row['pixel']),xyz_mm=point.tolist())
        for stage in ('above','grasp'):
            q=array(row[stage],(5,),'九点'+stage+'角度').tolist()
            item[stage]=[arm.angle(arms,i,v) for i,v in enumerate(q,1)]
        samples.append(item)
    points=np.array([s['xyz_mm'][:2] for s in samples])
    if np.linalg.matrix_rank(points-points.mean(axis=0))<2:raise ValueError('九点转换后退化，不能作为二维参考')
    cells=copy.deepcopy(report.get('fit',{}).get('cells',[]))
    if not cells:raise ValueError('九点缺少原始单元信息')
    result=dict(schema='nine_point_reference_v1',projection_version=calibration_version(cfg),
        source_cards_sha256=report['cards_sha256'],source_states_sha256=report['states_sha256'],
        baseline_frame_id=baseline['frame_id'],baseline_readings=angles,plane_z_mm=height,
        samples=samples,cells=cells,source_type='user_report',
        note='旧九点经现有标定换算位置，仅作求解参考；未拟合或改变相机/TCP，不代表完整抓放验收')
    result['version']=fingerprint(result)
    validate_reference(result,cfg,arms)
    return result


def validate_reference(reference,cfg,arms):
    from core import arm
    from manipulation_geometry import calibration_version,array
    if reference.get('schema')!='nine_point_reference_v1':raise ValueError('未知九点参考版本')
    raw={k:v for k,v in reference.items() if k!='version'}
    if reference.get('version')!=fingerprint(raw):raise ValueError('九点参考内容指纹不符')
    if reference.get('projection_version')!=calibration_version(cfg):raise ValueError('九点参考与当前标定/模型不一致')
    rows=reference.get('samples',[])
    if len(rows)!=9:raise ValueError('九点参考需要9个原始位置')
    for row in rows:
        array(row['xyz_mm'],(3,),'九点换算位置')
        for stage in ('above','grasp'):
            for i,v in enumerate(array(row[stage],(5,),'九点角度'),1):arm.angle(arms,i,float(v))
    for cell in reference['cells']:
        ids=cell['indices']
        if len(ids)!=3 or len(set(ids))!=3 or any(type(i)is not int or not 0<=i<9 for i in ids):raise ValueError('九点单元索引无效')


def reference_seed(reference,xy,stage):
    """Inside a triangle interpolate; outside use nearest point, never extrapolate."""
    points=np.asarray([s['xyz_mm'][:2] for s in reference['samples']]);xy=np.asarray(xy,float)
    for cell in reference['cells']:
        ids=cell['indices'];vertices=points[ids]
        try:weights=np.linalg.solve(np.vstack([vertices.T,np.ones(3)]),np.r_[xy,1])
        except np.linalg.LinAlgError:continue
        if np.all(weights>=-1e-10) and np.all(weights<=1+1e-10):
            values=np.asarray([reference['samples'][i][stage] for i in ids],float)
            q=values[0]+weights[1:]@(values[1:]-values[0])
            return dict(angles=q.tolist(),mode='interpolated',names=[reference['samples'][i]['name'] for i in ids],weights=weights.tolist())
    index=int(np.argmin(np.linalg.norm(points-xy,axis=1)))
    return dict(angles=reference['samples'][index][stage].copy(),mode='nearest',names=[reference['samples'][index]['name']],
                distance_mm=float(np.linalg.norm(points[index]-xy)))
