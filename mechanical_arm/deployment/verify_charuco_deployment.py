"""Read-only device verification; update the local reviewed-startup fingerprints."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/charuco_20261024'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old=json.loads((ROOT/'mac_tools/reviewed_sources.json').read_text())
    report=json.loads((OUT/'deployment_report.json').read_text())
    names=set(old)|set(report['code_files'])|{
        'sorting_demo/manipulation_ui.js','sorting_demo/manipulation_view.js',
        'sorting_demo/models/manipulation/manifest.json','sorting_demo/models/manipulation/cad_profile.json',
        'sorting_demo/models/manipulation/dofbot_display.urdf','sorting_demo/cad_geometry.py',
        'AGENTS.md','机械臂操作准则.md','calibration_runtime/manifest.json'}
    expected={name:sha(OUT/'runtime/manifest.json' if name=='calibration_runtime/manifest.json' else ROOT/name) for name in sorted(names)}
    request=json.dumps(dict(names=sorted(names),protected=report['preserved_user_json'],backup=report['backup']))
    remote=r'''
import hashlib,json,urllib.request,tarfile
from pathlib import Path
import cv2
request=json.loads(REQUEST)
root=Path('/root/jjt')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
get=lambda path:json.load(urllib.request.urlopen('http://192.168.137.100:8080'+path,timeout=10))
control=get('/control/status');cal=get('/calibration/status')
with tarfile.open(Path(request['backup'])/'project_before.tar.gz') as archive:
    audit_before=archive.extractfile('arm_playground/logs/web_control.jsonl').read()
audit_after=(root/'arm_playground/logs/web_control.jsonl').read_bytes()
appended=[json.loads(line) for line in audit_after[len(audit_before):].splitlines()]
payload=dict(hashes={n:sha(root/n) for n in request['names']},
    native_opencv=cv2.__version__,native_has_aruco=hasattr(cv2,'aruco'),
    busy=control['busy'],mode=control['mode'],cached_readings=len(control['joints']),
    commanded=control['commanded'],calibration_sessions=len(cal['sessions']),
    calibration_solutions=len(cal['solutions']),capability=cal['capability'],camera=get('/status'),
    web_control_sha256=sha(root/'arm_playground/logs/web_control.jsonl'),
    audit_prefix_preserved=audit_after.startswith(audit_before),
    new_audit_events=[dict(kind=e.get('kind'),operation=e.get('parameters',{}).get('operation')) for e in appended],
    preserved_user_json=all(sha(Path(p))==v for p,v in request['protected'].items()))
print(json.dumps(payload,ensure_ascii=False))
'''.replace('REQUEST',repr(request))
    command=['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=5','root@192.168.137.100',
        '/root/miniconda3/envs/ros-arm-env/bin/python -B -']
    result=subprocess.run(command,input=remote,text=True,capture_output=True,timeout=40)
    if result.returncode:raise RuntimeError(result.stderr)
    data=json.loads(result.stdout)
    mismatches=[name for name in names if expected[name]!=data['hashes'][name]]
    if mismatches:raise ValueError('本地与设备源码不同，未更新启动清单：'+str(mismatches))
    if not data['preserved_user_json'] or data['busy'] or not data['capability']['available']:
        raise ValueError('配置保留、空闲或识别能力检查未通过')
    worker=json.loads((OUT/'device_worker_test.json').read_text())['result']
    if not worker['acceptable'] or worker['corner_count']!=40 or worker['marker_count']!=27:
        raise ValueError('设备合成图识别未通过')
    before=json.loads((OUT/'audit_before.json').read_text())
    data['control_audit_unchanged']=data['web_control_sha256']==before['web_control_sha256']
    data['only_startup_audit_added']=data['audit_prefix_preserved'] and all(e==dict(kind='audit',operation='startup') for e in data['new_audit_events'])
    if not data['only_startup_audit_added'] or data['cached_readings'] or data['commanded']:
        raise ValueError('出现额外控制记录，请核对；未继续修改配置')
    data['synthetic_detection']=dict(corners=worker['corner_count'],markers=worker['marker_count'],opencv=worker['opencv_version'])
    data['software_only']=True
    data['physical_calibration_verified']=False
    (OUT/'verification_report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    backup=OUT/'before/reviewed_sources.json'
    if not backup.exists():backup.write_bytes((ROOT/'mac_tools/reviewed_sources.json').read_bytes())
    (ROOT/'mac_tools/reviewed_sources.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:data[k] for k in ('native_opencv','native_has_aruco','busy','mode','calibration_sessions',
        'calibration_solutions','capability','camera','preserved_user_json','only_startup_audit_added','synthetic_detection')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
