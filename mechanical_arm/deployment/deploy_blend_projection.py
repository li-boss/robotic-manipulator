"""Back up and update blended transit, projection plane, write hold and rule files while services are stopped."""
import base64,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/blend_projection_20261029'
FILES=('AGENTS.md','机械臂操作准则.md','arm_playground/arm.py','sorting_demo/manipulation_geometry.py','sorting_demo/manipulation_task.py','sorting_demo/charuco_task.py','sorting_demo/manipulation_ui.js')
if __name__=='__main__':
    manifest=ROOT/'mac_tools/reviewed_sources.json';reviewed=json.loads(manifest.read_text())
    request=dict(reviewed=reviewed,files={name:base64.b64encode((ROOT/name).read_bytes()).decode() for name in FILES})
    before=ROOT/'implementation_backups/blend_projection_20261029/before'
    for name in FILES:
        assert hashlib.sha256((before/Path(name).name).read_bytes()).hexdigest()==reviewed[name],'本地备份与已审阅清单不符：'+name
    program=r'''
import ast,base64,hashlib,json,socket,subprocess,tarfile,uuid
from pathlib import Path
root=Path('/root/jjt');request=json.loads(REQUEST)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def stopped():
    assert not (root/'arm_playground/.hardware.lock').exists(),'存在硬件锁，未部署'
    with socket.socket() as probe:
        probe.settimeout(2);assert probe.connect_ex(('192.168.137.100',8080))!=0,'网页正在运行，未覆盖'
    processes=subprocess.check_output(['ps','-eo','pid,args'],text=True).splitlines()
    assert not any('/root/jjt/sorting_demo/'+name in line for line in processes for name in ('server.py','ros_planner.py','start_service.py','planning_worker.py')),'项目进程仍在运行，未覆盖'
for name,value in request['reviewed'].items():
    assert sha(root/name)==value,'设备源码已有修改，保留：'+name
raw={name:base64.b64decode(value) for name,value in request['files'].items()}
assert set(raw)==set(request['files'])
for name,value in raw.items():
    if name.endswith('.py'):ast.parse(value)
stopped()
protected=[*list((root/'sorting_demo').glob('*.json')),root/'arm_playground/config.json',root/'arm_playground/manual_states.json']
protected=[p for p in protected if p.is_file()];original={str(p):sha(p) for p in protected}
audit=root/'arm_playground/logs/web_control.jsonl';audit_before=audit.read_bytes()
backup=root/'backups'/('blend_projection_20261029_'+uuid.uuid4().hex[:12]);backup.mkdir(parents=True)
with tarfile.open(str(backup/'source_and_data_before.tar.gz'),'w:gz') as archive:
    for name in raw:archive.add(str(root/name),arcname=name)
    for p in protected:archive.add(str(p),arcname=str(p.relative_to(root)))
(backup/'control_audit_before.jsonl').write_bytes(audit_before)
(backup/'protected_before.json').write_text(json.dumps(original,indent=2))
previous={name:(root/name).read_bytes() for name in raw}
stopped()
assert all(sha(Path(p))==v for p,v in original.items()),'备份期间资料变化'
try:
    for name,value in raw.items():
        target=root/name;tmp=target.with_name(target.name+'.sync_tmp');tmp.write_bytes(value);tmp.replace(target)
    assert all(sha(Path(p))==v for p,v in original.items())
    assert audit.read_bytes()==audit_before
except Exception:
    for name,value in previous.items():(root/name).write_bytes(value)
    raise
report=dict(backup=str(backup),files={name:sha(root/name) for name in raw},protected_json_unchanged=True,audit_unchanged=True,hardware_reads=0,motor_commands=0,services_started=False)
(backup/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
'''.replace('REQUEST',repr(json.dumps(request)))
    (OUT/'reviewed_sources_before.json').write_bytes(manifest.read_bytes())
    r=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=5','root@192.168.137.100','/root/miniconda3/envs/ros-arm-env/bin/python -B -'],input=program,text=True,capture_output=True,timeout=30)
    (OUT/'deployment.log').write_text(r.stdout+r.stderr)
    if r.returncode:raise RuntimeError(r.stderr)
    report=json.loads(r.stdout)
    for name,value in report['files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value
    reviewed.update(report['files']);manifest.write_text(json.dumps(reviewed,ensure_ascii=False,indent=2)+'\n')
    (OUT/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
