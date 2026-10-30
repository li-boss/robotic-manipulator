"""Reviewed 10 mm / 2 degree change; restart idle camera web server only."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/hand_eye_tolerance_20261024'
FILES=('sorting_demo/charuco_geometry.py','sorting_demo/charuco_task.py','sorting_demo/calibration_ui.js','sorting_demo/相机标定使用说明.md')


if __name__=='__main__':
    reviewed_path=ROOT/'mac_tools/reviewed_sources.json'
    reviewed=json.loads(reviewed_path.read_text())
    request=dict(files={n:base64.b64encode((ROOT/n).read_bytes()).decode() for n in FILES},
        previous={n:reviewed[n] for n in FILES},
        rules={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('AGENTS.md','机械臂操作准则.md')})
    program=r'''
import ast,base64,hashlib,json,os,signal,socket,subprocess,tarfile,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
root=Path('/root/jjt');request=json.loads(REQUEST)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def get(path):
    with urllib.request.urlopen('http://192.168.137.100:8080'+path,timeout=8) as response:return json.load(response)
def idle():
    state=get('/control/status')
    assert not state['busy'] and not (root/'arm_playground/.hardware.lock').exists(),'控制忙碌，未部署'
    return state
for name,value in request['rules'].items():
    assert (root/name).read_text().strip() and sha(root/name)==value,'规则不同，未部署'
before=idle()
raw={name:base64.b64decode(value) for name,value in request['files'].items()}
for name,value in raw.items():
    assert name in ('sorting_demo/charuco_geometry.py','sorting_demo/charuco_task.py','sorting_demo/calibration_ui.js','sorting_demo/相机标定使用说明.md')
    assert sha(root/name)==request['previous'][name],'现场源码变化，保留用户修改'
    if name.endswith('.py'):ast.parse(value)
pidpath=root/'sorting_demo/server.pid';pid=int(pidpath.read_text())
proc=Path('/proc')/str(pid)
command=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
assert str(root/'sorting_demo/server.py') in command and command[0]=='/root/miniconda3/envs/ros-arm-env/bin/python'
environment=dict(item.decode().split('=',1) for item in (proc/'environ').read_bytes().split(b'\0') if item)
protected=list((root/'sorting_demo').glob('*.json'))+[root/'arm_playground/config.json',root/'arm_playground/manual_states.json']
original={str(p):sha(p) for p in protected if p.is_file()}
audit=root/'arm_playground/logs/web_control.jsonl';audit_before=audit.read_bytes()
backup=root/'backups'/('hand_eye_tolerance_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
backup.mkdir(parents=True)
with tarfile.open(str(backup/'source_and_data_before.tar.gz'),'w:gz') as archive:
    for name in raw:archive.add(str(root/name),arcname=name)
    for p in protected:
        if p.is_file():archive.add(str(p),arcname=str(p.relative_to(root)))
(backup/'control_audit_before.jsonl').write_bytes(audit_before)
(backup/'protected_before.json').write_text(json.dumps(original,indent=2))
previous={name:(root/name).read_bytes() for name in raw}
idle()
assert all(sha(Path(p))==v for p,v in original.items()),'备份期间资料改变，未部署'
os.kill(pid,signal.SIGTERM)
for _ in range(100):
    cmdline=proc/'cmdline'
    if not cmdline.exists() or not cmdline.read_bytes():break
    time.sleep(.1)
else:raise ValueError('旧服务未退出，未覆盖')
def start():
    with (root/'sorting_demo/logs/server.log').open('ab') as log:
        child=subprocess.Popen(command,cwd=str(root/'sorting_demo'),env=environment,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    pidpath.write_text(str(child.pid)+'\n')
    for _ in range(100):
        if child.poll() is not None:raise RuntimeError('网页服务启动失败')
        try:
            state=get('/control/status')
            if state['csrf']!=before['csrf']:return child,state
        except (OSError,ValueError):pass
        time.sleep(.2)
    child.terminate();child.wait(timeout=10)
    raise RuntimeError('网页服务就绪超时')
try:
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(('192.168.137.100',8080))!=0:break
        time.sleep(.1)
    else:raise ValueError('端口仍占用，未覆盖')
    for name,value in raw.items():
        target=root/name;tmp=target.with_name(target.name+'.tolerance_tmp')
        tmp.write_bytes(value);tmp.replace(target)
    child,after=start()
except Exception:
    for name,value in previous.items():(root/name).write_bytes(value)
    start()
    raise
assert all(sha(Path(p))==v for p,v in original.items()),'现场数据变化，请核对'
audit_after=audit.read_bytes()
assert audit_after.startswith(audit_before),'控制审计前缀变化'
added=[json.loads(line) for line in audit_after[len(audit_before):].splitlines() if line]
assert all(e.get('kind')=='audit' and e.get('parameters',{}).get('operation')=='startup' for e in added),'出现额外控制记录，请核对'
cal=get('/calibration/status')
report=dict(backup=str(backup),files={n:sha(root/n) for n in raw},preserved_user_json=True,
    old_results_preserved=True,mode=after['mode'],busy=after['busy'],capability=cal['capability'],
    audit_prefix_preserved=True,new_audit_operations=[e['parameters']['operation'] for e in added],
    hardware_reads=0,motor_commands=0,server_pid=child.pid)
(backup/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
'''.replace('REQUEST',repr(json.dumps(request)))
    (OUT/'reviewed_sources_before.json').write_bytes(reviewed_path.read_bytes())
    result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=5','root@192.168.137.100',
        '/root/miniconda3/envs/ros-arm-env/bin/python -B -'],input=program,text=True,capture_output=True,timeout=50)
    (OUT/'deployment.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(result.stderr)
    report=json.loads(result.stdout)
    (OUT/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for name,value in report['files'].items():assert value==hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    reviewed.update(report['files'])
    reviewed_path.write_text(json.dumps(reviewed,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
