"""Apply the user's confirmed grey-table datum: 80 mm model minus 80 mm measured.

Only status GETs and /calibration/table-height; no camera capture or servo IO.
"""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/measured_table_save_20261024'
BASE='http://192.168.137.100:8080'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as r:return json.load(r)


if __name__=='__main__':
    for name in ('AGENTS.md','机械臂操作准则.md'):
        assert (ROOT/name).read_text().strip()
    control=get('/control/status');assert not control['busy'],'控制忙碌，未修改'
    before=get('/manipulation/status');cal=get('/calibration/status')
    assert before['setup']['table_z_mm']==-16,'当前高度不是待修改的旧值，保留现场配置'
    expected_points={s['id']:s['point_checks'] for s in cal['sessions'] if s['kind']=='position'}
    photo_hash=hashlib.sha256((ROOT/'sorting_demo/IMG_2710.heic').read_bytes()).hexdigest()
    (OUT/'before_height_change.json').write_text(json.dumps(dict(setup=before['setup'],
        calibration=cal),ensure_ascii=False,indent=2)+'\n')
    fields=dict(csrf=control['csrf'],revision=cal['revision'],setup_sha256=before['setup_sha256'],
        confirmed=True,measured_height_mm=80,
        work_surface='灰色桌面（白色设备箱下方）；用户确认到蓝圈圆形安装板上平面的高度为80毫米',
        photo_sha256=photo_hash)
    request=urllib.request.Request(BASE+'/calibration/table-height',data=json.dumps(fields).encode(),
        headers={'Content-Type':'application/json','Origin':BASE},method='POST')
    with urllib.request.urlopen(request,timeout=30) as response:updated=json.load(response)
    after=get('/manipulation/status');cfg=after['setup'];source=cfg['cad']['camera_calibration']['table_height_source']
    assert cfg['table_z_mm']==0 and source['kind']=='measured_model_datum' and source['photo_sha256']==photo_hash
    assert cfg['cad']['camera_mount']==before['setup']['cad']['camera_mount'],'相机安装参数变化，请核对'
    assert {s['id']:s['point_checks'] for s in updated['sessions'] if s['kind']=='position'}==expected_points
    assert cfg['model_reviewed'] is False and cfg['calibration_reviewed'] is False
    assert not get('/control/status')['busy']
    report=dict(previous_table_z_mm=-16,current_table_z_mm=cfg['table_z_mm'],source=source,
        old_position_points_preserved=sum(len(v) for v in expected_points.values()),
        calibration_revision=updated['revision'],adopted=updated['adopted'],
        setup_sha256=after['setup_sha256'],hardware_reads=0,motor_commands=0)
    (OUT/'height_change_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
