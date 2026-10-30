"""User-authorized correction of the Y=213 typo and scoped 5 mm table result.

No camera/servo reads, no movement; original point is archived by the service.
"""
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/table_three_point_20261024'
BASE='http://192.168.137.100:8080'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as response:return json.load(response)


def post(path,fields,csrf,revision):
    body=dict(fields,csrf=csrf,revision=revision)
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','Origin':BASE},method='POST')
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


if __name__=='__main__':
    for name in ('AGENTS.md','机械臂操作准则.md'):assert (ROOT/name).read_text().strip()
    control=get('/control/status');assert not control['busy'],'控制忙碌，未修改'
    before_setup=get('/manipulation/status');before=get('/calibration/status')
    matches=[s for s in before['sessions'] if s['id'].startswith('7851fd00')]
    assert len(matches)==1
    batch=matches[0];points=batch['point_checks'];assert len(points)==4,'批次点数改变，请重新核对'
    bad,corrected=points[-2:]
    assert abs(bad['error_mm']-19.733899371973994)<.0001
    assert bad['actual_xyz_mm']==[0,213,0] and corrected['actual_xyz_mm']==[0,233,0]
    assert bad['capture_id']==corrected['capture_id'] and bad['pixel']==corrected['pixel']
    kept=points[:2]+points[-1:];assert all(r['kind']=='table' and r['error_mm']<=5 for r in kept)
    (OUT/'batch_before.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n')
    after=post('/calibration/exclude-point',dict(session_id=batch['id'],record_sha256=bad['record_sha256'],
        reason='用户确认此条实测Y误录为213毫米；同图同像素下一条已更正为233毫米。按用户要求移出当前结果，保留原始记录。'),
        control['csrf'],before['revision'])
    current=next(s for s in after['sessions'] if s['id']==batch['id'])
    assert current['point_checks']==kept
    after=post('/calibration/adopt-position',dict(session_id=batch['id'],limit_mm=5,scope='table',confirmed=True),
        control['csrf'],after['revision'])
    result=after['position_result'];current=next(s for s in after['sessions'] if s['id']==batch['id'])
    assert result['valid'] and result['scope']=='table' and result['point_count']==3 and result['limit_mm']==5
    assert current['report']['table_passed'] and not current['report']['top_passed'] and not current['report']['passed']
    after_setup=get('/manipulation/status')
    assert after_setup['setup_sha256']==before_setup['setup_sha256'],'运动/定位配置变化，请核对'
    report=dict(session_id=batch['id'],result=result,report=current['report'],
        kept_points=current['point_checks'],excluded_records=current['excluded_point_checks'],
        unchanged_setup_sha256=after_setup['setup_sha256'],
        unchanged_motion_ik_tolerance_mm=after_setup['setup']['max_position_error_mm'],
        hardware_reads=0,motor_commands=0)
    (OUT/'applied_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(session_id=batch['id'],scope=result['scope'],limit_mm=5,
        errors_mm=[r['error_mm'] for r in kept],max_error_mm=result['max_error_mm'],
        table_passed=current['report']['table_passed'],top_passed=current['report']['top_passed'],
        result_valid=result['valid'],excluded_count=len(current['excluded_point_checks']),
        unchanged_motion_ik_tolerance_mm=after_setup['setup']['max_position_error_mm'],
        hardware_reads=0,motor_commands=0),ensure_ascii=False,indent=2))
