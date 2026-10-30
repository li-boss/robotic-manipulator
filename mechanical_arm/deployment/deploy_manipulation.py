"""设备端受限部署：备份、文件校验与原服务重启。无Arm_Lib、舵机读取或运动接口。"""
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


def status():
    with urllib.request.urlopen(URL+'/control/status',timeout=5) as response:return json.load(response)


def main():
    archive=Path(sys.argv[1]).resolve();expected=sys.argv[2]
    if ROOT not in archive.parents or sha(archive)!=expected:raise ValueError('部署包路径或散列不符')
    for name in ('AGENTS.md','机械臂操作准则.md'):
        path=ROOT/name
        if not path.read_text().strip():raise ValueError('缺少操作规则')
        print('本次规则核对',name,sha(path),flush=True)
    print('本次参数：仅部署并重启已闲置服务；不读取舵机、不运动、不切换扭矩。',flush=True)
    state=status()
    if state['busy']:raise ValueError('设备任务正在执行，未部署')
    if (ROOT/'arm_playground/.hardware.lock').exists():raise ValueError('存在硬件锁，未部署或清锁')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    stage=ROOT/'staging'/('manipulation_'+stamp);stage.mkdir(parents=True)
    with tarfile.open(archive,'r:gz') as source:
        manifest=json.load(source.extractfile('deployment_manifest.json'))
        for name,info in manifest['files'].items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or (relative.parts[0]!='sorting_demo' and name not in ('AGENTS.md','机械臂操作准则.md')) or 'logs' in relative.parts:
                raise ValueError('非法部署目标')
            member=source.getmember(name)
            if not member.isfile():raise ValueError('部署包只能包含普通文件')
            data=source.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=info['sha256']:raise ValueError('部署文件散列不符')
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            if relative.suffix=='.py':ast.parse(data,filename=name)
            current=ROOT/relative
            if current.exists() and name!='sorting_demo/manipulation_setup.json':
                if info.get('previous_sha256') is None and sha(current)!=info['sha256']:
                    raise ValueError('已有新模块存在不同内容，保留现场文件：'+name)
                if info.get('previous_sha256') and sha(current)!=info['previous_sha256']:
                    raise ValueError('已有代码被修改，保留现场修改：'+name)
    protected=[ROOT/'arm_playground/config.json',ROOT/'arm_playground/manual_states.json']
    protected+=list((ROOT/'sorting_demo').glob('*.json'))
    original_data={str(p):sha(p) for p in protected if p.exists()}
    pids=[]
    for label in ('server','planner'):
        file=ROOT/'sorting_demo'/(label+'.pid')
        if not file.exists():continue
        pid=int(file.read_text().strip());cmdline=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        script=str(ROOT/'sorting_demo'/('server.py' if label=='server' else 'ros_planner.py')).encode()
        if script not in cmdline:raise ValueError('PID与已核对服务不符')
        pids.append((label,pid))
    if not any(label=='server' for label,_ in pids):raise ValueError('未核对到现有服务PID')
    if status()['busy']:raise ValueError('部署前再次检查发现设备忙，未停止服务')
    for _,pid in pids:os.kill(pid,signal.SIGTERM)
    for _ in range(50):
        with socket.socket() as probe:
            probe.settimeout(.2)
            if probe.connect_ex(('192.168.137.100',8080))!=0:break
        time.sleep(.1)
    else:raise ValueError('原端口仍被占用，未覆盖文件')
    backup=ROOT/'backups'/('manipulation_'+stamp);backup.mkdir(parents=True)
    with tarfile.open(backup/'project_before.tar.gz','w:gz') as output:
        for directory in ('sorting_demo','arm_playground'):output.add(ROOT/directory,arcname=directory)
        for name in ('AGENTS.md','机械臂操作准则.md'):output.add(ROOT/name,arcname=name)
    old_files={}
    command=[PYTHON,'-B',str(ROOT/'sorting_demo/start_service.py')]
    if state['mode']=='real':command.append('--enable-real-control')
    if any(label=='planner' for label,_ in pids):command.append('--enable-ros-planner')
    try:
        for name in manifest['files']:
            target=ROOT/name
            if target.exists():old_files[name]=target.read_bytes()
            if name=='sorting_demo/manipulation_setup.json' and target.exists():continue
            previous=manifest['files'][name].get('previous_sha256')
            if target.exists() and sha(target)!=(previous or manifest['files'][name]['sha256']):
                raise ValueError('覆盖前发现文件变化，保留现场修改：'+name)
            if ROOT not in target.resolve().parents:raise ValueError('目标指向项目外部')
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name(target.name+'.deploy_tmp');temp.write_bytes((stage/name).read_bytes())
            os.chmod(temp,(target.stat().st_mode&0o777) if target.exists() else 0o600)
            temp.replace(target)
        if any(sha(Path(p))!=v for p,v in original_data.items()):raise ValueError('现场JSON资料改变，停止部署')
        started=subprocess.run(command,capture_output=True,text=True,timeout=60,cwd=ROOT/'sorting_demo')
        (backup/'startup.log').write_text(started.stdout+started.stderr)
        if started.returncode:raise RuntimeError('启动失败，见'+str(backup/'startup.log'))
        if status()['busy']:raise RuntimeError('启动后任务状态异常')
        with urllib.request.urlopen(URL+'/manipulation/status',timeout=10) as response:ready=json.load(response)
        report=dict(deployed=True,backup=str(backup),archive_sha256=expected,
            mode=ready['mode'],execution_ready=ready['execution_readiness']['ready'],
            preserved_user_json=original_data,files={name:sha(ROOT/name) for name in manifest['files']},
            hardware_reads=0,motor_commands=0)
        (backup/'deployment_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:report[k] for k in ('deployed','backup','mode','execution_ready','hardware_reads','motor_commands')},ensure_ascii=False),flush=True)
    except Exception:
        # 启动失败时只恢复代码；现场JSON不覆盖。恢复本身不包含硬件动作。
        with socket.socket() as probe:
            probe.settimeout(.2);running=probe.connect_ex(('192.168.137.100',8080))==0
        if running:
            current=status()
            if current['busy']:raise RuntimeError('新服务出现活动任务，未停止或覆盖运行代码；请现场核对备份')
            for label in ('server','planner'):
                pidfile=ROOT/'sorting_demo'/(label+'.pid')
                if not pidfile.exists():continue
                pid=int(pidfile.read_text());proc=Path(f'/proc/{pid}/cmdline')
                script=str(ROOT/'sorting_demo'/('server.py' if label=='server' else 'ros_planner.py')).encode()
                if proc.exists() and script in proc.read_bytes().split(b'\0'):os.kill(pid,signal.SIGTERM)
            time.sleep(.5)
        for name,data in old_files.items():
            if not name.endswith('.json'):(ROOT/name).write_bytes(data)
        with socket.socket() as probe:
            probe.settimeout(.2);running=probe.connect_ex(('192.168.137.100',8080))==0
        if not running:
            restarted=subprocess.run(command,capture_output=True,text=True,timeout=60,cwd=ROOT/'sorting_demo')
            (backup/'rollback_startup.log').write_text(restarted.stdout+restarted.stderr)
        raise


if __name__=='__main__':main()
