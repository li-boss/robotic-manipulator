"""Device deployment: reviewed files + isolated image dependencies, no arm reads/writes."""
import ast
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT=Path('/root/jjt')
PYTHON='/root/miniconda3/envs/ros-arm-env/bin/python'
URL='http://192.168.137.100:8080'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def get(path):
    with urllib.request.urlopen(URL+path,timeout=8) as response:return json.load(response)


def listening():
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('192.168.137.100',8080))==0


def processes():
    found=[]
    for role in ('server','planner'):
        pidfile=ROOT/'sorting_demo'/(role+'.pid')
        if not pidfile.exists():continue
        pid=int(pidfile.read_text().strip())
        proc=Path('/proc')/str(pid)/'cmdline'
        if not proc.exists():continue
        expected=str(ROOT/'sorting_demo'/('server.py' if role=='server' else 'ros_planner.py')).encode()
        if expected not in proc.read_bytes().split(b'\0'):
            raise ValueError('已有PID被其他进程使用，未停止：'+role)
        found.append((role,pid))
    return found


def stage(archive,checksum,destination,prefix):
    if ROOT not in archive.resolve().parents or sha(archive)!=checksum:
        raise ValueError('部署包路径或散列错误')
    with tarfile.open(archive,'r:gz') as source:
        manifest=json.load(source.extractfile('deployment_manifest.json'))
        for name,info in manifest['files'].items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or not name.startswith(prefix+'/') or 'logs' in relative.parts:
                raise ValueError('非法部署文件：'+name)
            member=source.getmember(name)
            if not member.isfile() or member.size>128*1024*1024:
                raise ValueError('部署包只能包含有限普通文件')
            raw=source.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest()!=info['sha256']:
                raise ValueError('文件散列错误：'+name)
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            if prefix=='sorting_demo' and target.suffix=='.py':ast.parse(raw,filename=name)
    return manifest['files']


def main():
    if len(sys.argv)!=5:raise ValueError('需要code包、散列、runtime包、散列')
    for name in ('AGENTS.md','机械臂操作准则.md'):
        p=ROOT/name
        if not p.read_text().strip():raise ValueError('缺少操作准则')
        print('核对规则',name,sha(p),flush=True)
    print('本次仅备份部署与启动实机网页/纯计算ROS；不读取舵机、不运动、不切换扭矩。',flush=True)
    if (ROOT/'arm_playground/.hardware.lock').exists():raise ValueError('存在硬件锁，不部署、不删锁')
    online=listening();pids=processes()
    if online:
        state=get('/control/status')
        if state['busy'] or not any(role=='server' for role,_ in pids):raise ValueError('服务忙或PID不符')
        mode=state['mode'];ros=any(role=='planner' for role,_ in pids)
    else:
        if pids:raise ValueError('服务进程存在但端口不可用，请先核对，不自动结束')
        mode='real';ros=True
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    staging=ROOT/'staging'/('charuco_'+stamp);staging.mkdir(parents=True)
    code=stage(Path(sys.argv[1]),sys.argv[2],staging,'sorting_demo')
    runtime=stage(Path(sys.argv[3]),sys.argv[4],staging,'calibration_runtime')
    for name,info in code.items():
        path=ROOT/name
        if path.exists() and sha(path) not in (info.get('previous_sha256'),info['sha256']):
            raise ValueError('现场代码已改变，保留用户修改：'+name)
    if (ROOT/'calibration_runtime').exists():
        for name,info in runtime.items():
            if not (ROOT/name).is_file() or sha(ROOT/name)!=info['sha256']:
                raise ValueError('已有独立识别组件不同，保留现场目录')
    protected=list((ROOT/'sorting_demo').glob('*.json'))+[ROOT/'arm_playground/config.json',ROOT/'arm_playground/manual_states.json']
    original={str(p):sha(p) for p in protected if p.exists()}
    if online and get('/control/status')['busy']:raise ValueError('设备开始执行，未停止服务')
    backup=ROOT/'backups'/('charuco_'+stamp);backup.mkdir(parents=True)
    with tarfile.open(backup/'project_before.tar.gz','w:gz') as archive:
        for name in ('sorting_demo','arm_playground','AGENTS.md','机械臂操作准则.md'):
            archive.add(ROOT/name,arcname=name)
    previous={name:(ROOT/name).read_bytes() if (ROOT/name).exists() else None for name in code}
    if online:
        if get('/control/status')['busy'] or (ROOT/'arm_playground/.hardware.lock').exists():
            raise ValueError('备份期间出现控制任务或硬件锁，未停止服务')
        for _,pid in pids:os.kill(pid,signal.SIGTERM)
        for _ in range(50):
            if not listening():break
            time.sleep(.1)
        else:raise ValueError('旧端口仍占用，未覆盖代码')
    command=[PYTHON,'-B',str(ROOT/'sorting_demo/start_service.py')]
    if mode=='real':command.append('--enable-real-control')
    if ros:command.append('--enable-ros-planner')
    try:
        if not (ROOT/'calibration_runtime').exists():
            (staging/'calibration_runtime').replace(ROOT/'calibration_runtime')
        for name,info in code.items():
            target=ROOT/name
            if target.exists() and sha(target) not in (info.get('previous_sha256'),info['sha256']):raise ValueError('部署期间代码变化：'+name)
            temporary=target.with_name(target.name+'.deploy_tmp');temporary.write_bytes((staging/name).read_bytes());temporary.replace(target)
        if any(sha(Path(p))!=checksum for p,checksum in original.items()):raise ValueError('现场JSON变化，停止部署')
        environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT/'calibration_runtime/site-packages')
        check=subprocess.run([PYTHON,'-B','-c',
            'import cv2; from charuco_geometry import make_board; b=make_board(); assert len(b.getIds())==27; print(cv2.__version__)'],
            env=environment,cwd=ROOT/'sorting_demo',capture_output=True,text=True,timeout=30)
        (backup/'runtime_check.log').write_text(check.stdout+check.stderr)
        if check.returncode:raise RuntimeError('独立识别组件自检失败，见'+str(backup/'runtime_check.log'))
        result=subprocess.run(command,capture_output=True,text=True,timeout=60,cwd=ROOT/'sorting_demo')
        (backup/'startup.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('新服务启动失败，见'+str(backup/'startup.log'))
        state=get('/control/status');calibration=get('/calibration/status')
        if state['busy'] or not calibration['capability']['available']:raise ValueError('新服务忙或识别组件未就绪')
        report=dict(deployed=True,backup=str(backup),mode=state['mode'],code_files={name:sha(ROOT/name) for name in code},
            runtime_manifest_sha256=sha(ROOT/'calibration_runtime/manifest.json'),preserved_user_json=original,
            camera=get('/status'),calibration=calibration,hardware_reads=0,motor_commands=0)
        (backup/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(dict(deployed=True,backup=str(backup),mode=state['mode'],capability=calibration['capability'],hardware_reads=0,motor_commands=0),ensure_ascii=False),flush=True)
    except Exception:
        if listening():
            if get('/control/status')['busy']:raise RuntimeError('任务已开始，未停止或覆盖；请核对备份')
            for _,pid in processes():os.kill(pid,signal.SIGTERM)
            time.sleep(.5)
        for name,raw in previous.items():
            target=ROOT/name
            if raw is None:
                if target.exists():target.unlink()
            else:target.write_bytes(raw)
        if online and not listening():
            result=subprocess.run(command,capture_output=True,text=True,timeout=60,cwd=ROOT/'sorting_demo')
            (backup/'rollback.log').write_text(result.stdout+result.stderr)
        raise


if __name__=='__main__':main()
