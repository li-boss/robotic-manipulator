"""Apply user-authorized 8-mm position budget via settings API. No servo/camera reads."""
import copy,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'implementation_backups/planner_tolerance_8mm_20261028';BASE='http://192.168.137.100:8080'
def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as r:return json.load(r)
if __name__=='__main__':
    for name in ('AGENTS.md','机械臂操作准则.md'):assert (ROOT/name).read_text().strip()
    control=get('/control/status');before=get('/manipulation/status');cal=get('/calibration/status');nine=get('/teaching/status')
    assert not control['busy'] and (before.get('planning') or {}).get('status')!='running'
    setup=copy.deepcopy(before['setup']);assert setup['max_position_error_mm']==1.,'位置容差已有现场修改，未覆盖'
    assert setup['model_reviewed'] is False and setup['calibration_reviewed'] is False and setup['path_validations']==[]
    assert setup['cad']['assumptions_confirmed'] is False
    desired=copy.deepcopy(setup);desired['max_position_error_mm']=8.
    (OUT/'settings_before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2))
    (OUT/'calibration_before.json').write_text(json.dumps(cal,ensure_ascii=False,indent=2))
    request=urllib.request.Request(BASE+'/manipulation/settings',data=json.dumps(dict(csrf=control['csrf'],setup=desired,expected_sha256=before['setup_sha256'])).encode(),headers={'Content-Type':'application/json','Origin':BASE},method='POST')
    with urllib.request.urlopen(request,timeout=30) as r:after=json.load(r)
    assert after['setup']==desired,'设置接口出现预期外变化，请核对'
    following=get('/calibration/status');following_nine=get('/teaching/status')
    assert following['adopted']==cal['adopted']
    assert following['position_result']==cal['position_result'],'位置检查记录变化，请核对'
    assert following_nine['setup']==nine['setup'],'旧九点资料变化，请核对'
    assert after['position_check']['valid'] and after['nine_point_reference']['available']
    report=dict(position_tolerance_mm=8.,direction_tolerance_deg=after['setup']['max_direction_error_deg'],
        jaw_open_width_mm=after['setup']['jaw_open_width_mm'],changed_setup_keys=['max_position_error_mm'],
        calibration_unchanged=True,nine_point_data_unchanged=True,position_result_unchanged=True,
        position_result_limit_mm=after['position_check']['limit_mm'],new_reference_version=after['nine_point_reference']['version'],
        hardware_reads=0,motor_commands=0)
    (OUT/'settings_after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2));(OUT/'applied_settings.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
