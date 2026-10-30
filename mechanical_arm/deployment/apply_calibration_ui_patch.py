"""Atomically deploy the reviewed static loading fix; no hardware/control mutation."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/charuco_20261024'


if __name__=='__main__':
    prior=json.loads((OUT/'deployment_report.json').read_text())
    request=json.dumps(dict(previous=prior['code_files']['sorting_demo/calibration_ui.js'],
        current=hashlib.sha256((ROOT/'sorting_demo/calibration_ui.js').read_bytes()).hexdigest(),backup=prior['backup']))
    program=r'''
import hashlib,json,urllib.request
from pathlib import Path
root=Path('/root/jjt');request=json.loads(REQUEST)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name in ('AGENTS.md','机械臂操作准则.md'):
    assert (root/name).read_text().strip();print('规则核对',name,sha(root/name))
with urllib.request.urlopen('http://192.168.137.100:8080/control/status',timeout=8) as response:
    status=json.load(response)
if status['busy']:raise ValueError('设备有控制任务，未修改页面')
source=root/'staging/charuco_ui_final.js';target=root/'sorting_demo/calibration_ui.js'
if sha(source)!=request['current']:raise ValueError('暂存页面散列不符')
if sha(target) not in (request['previous'],request['current']):raise ValueError('页面被修改，保留现场文件')
backup=Path(request['backup'])
if root not in backup.resolve().parents:raise ValueError('备份必须在项目中')
old=backup/'calibration_ui.before_load_commit.js'
if not old.exists():old.write_bytes(target.read_bytes())
temporary=target.with_suffix('.patch_tmp');temporary.write_bytes(source.read_bytes());temporary.replace(target)
report=dict(file='sorting_demo/calibration_ui.js',previous=request['previous'],current=sha(target),
    change='start finite preview after document load; schedule next preview after prior image completes',hardware_reads=0,motor_commands=0)
(backup/'ui_patch_report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
'''.replace('REQUEST',repr(request))
    result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=5','root@192.168.137.100',
        '/root/miniconda3/envs/ros-arm-env/bin/python -B -'],input=program,text=True,capture_output=True,timeout=25)
    (OUT/'ui_patch.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(result.stderr)
    print(result.stdout)
