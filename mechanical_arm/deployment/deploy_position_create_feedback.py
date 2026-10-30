"""Deploy three reviewed static files only; never read or command servos."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'implementation_backups/position_create_feedback_20261024'


if __name__ == '__main__':
    previous = json.loads((OUT / 'previous_hashes.json').read_text())
    reviewed_path = ROOT / 'mac_tools/reviewed_sources.json'
    reviewed = json.loads(reviewed_path.read_text())
    assert all(reviewed[name] == value for name, value in previous.items())
    payload = dict(previous=previous, rules={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        for name in ('AGENTS.md', '机械臂操作准则.md')},
        files={name: base64.b64encode((ROOT/name).read_bytes()).decode() for name in previous})
    program = r'''
import base64,hashlib,json,os,tarfile,urllib.request
from datetime import datetime,timezone
from pathlib import Path
root=Path('/root/jjt');request=json.loads(REQUEST)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,expected in request['rules'].items():
    assert (root/name).read_text().strip() and sha(root/name)==expected, '现场规则变化，未部署'
def idle():
    with urllib.request.urlopen('http://192.168.137.100:8080/control/status',timeout=8) as r:s=json.load(r)
    assert not s['busy'] and not (root/'arm_playground/.hardware.lock').exists(), '设备忙，未部署'
    return s
before=idle()
raw={name:base64.b64decode(value) for name,value in request['files'].items()}
for name,value in raw.items():
    assert name in ('sorting_demo/calibration.html','sorting_demo/calibration_ui.js','sorting_demo/相机标定使用说明.md')
    assert sha(root/name) in (request['previous'][name],hashlib.sha256(value).hexdigest()), '现场文件变化，保留用户修改'
protected=list((root/'sorting_demo').glob('*.json'))+[root/'arm_playground/config.json',root/'arm_playground/manual_states.json']
protected+=[root/'arm_playground/logs/web_control.jsonl']
original={str(p):sha(p) for p in protected if p.is_file()}
backup=root/'backups'/('position_create_feedback_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
backup.mkdir(parents=True)
(backup/'protected_before.json').write_text(json.dumps(original,indent=2))
with tarfile.open(str(backup/'source_before.tar.gz'),'w:gz') as archive:
    for name in raw:archive.add(str(root/name),arcname=name)
idle()
for name,value in raw.items():
    target=root/name;temporary=target.with_name(target.name+'.manual_tmp')
    temporary.write_bytes(value);os.chmod(str(temporary),target.stat().st_mode & 0o777);temporary.replace(target)
after=idle()
assert all(sha(Path(name))==value for name,value in original.items()), '运行数据发生变化，请核对现场'
report=dict(backup=str(backup),files={name:sha(root/name) for name in raw},
    protected_files=original,mode=after['mode'],hardware_reads=0,motor_commands=0,
    service_restarted=False,configuration_and_logs_unchanged=True)
(backup/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
'''.replace('REQUEST', repr(json.dumps(payload)))
    result = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
        'root@192.168.137.100', '/root/miniconda3/envs/ros-arm-env/bin/python -B -'],
        input=program, text=True, capture_output=True, timeout=30)
    (OUT/'deployment.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError(result.stderr)
    report = json.loads(result.stdout)
    (OUT/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for name,value in report['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value
    (OUT/'reviewed_sources_before.json').write_bytes(reviewed_path.read_bytes())
    reviewed.update(report['files'])
    reviewed_path.write_text(json.dumps(reviewed,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
