"""Mac SSH 启动入口：仅核对/启动服务，不调用机械臂控制接口。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
REMOTE = r'''
import hashlib, json, os, socket, subprocess, sys
from pathlib import Path
from urllib.request import urlopen
request = json.loads(REQUEST_JSON)
root = Path('/root/jjt')
os.chdir(root)
for rel, expected in request['sources'].items():
    path = root / rel
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('文件已变化或缺失，请重新审阅，未启动：' + rel)
print('设备规则及已审阅源码指纹一致；不会覆盖配置、读取舵机或发送运动。', flush=True)
print('实际参数：', {k: request[k] for k in ('start', 'real')}, flush=True)
commands = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True).splitlines()
services = [s for s in commands if any('/root/jjt/sorting_demo/' + name in s
            for name in ('server.py', 'ros_planner.py', 'start_service.py'))]
print('现有服务：', services, flush=True)
with socket.socket() as probe:
    probe.settimeout(2)
    listening = probe.connect_ex(('192.168.137.100', 8080)) == 0
if request['start'] and not listening:
    if services or (root / 'arm_playground/.hardware.lock').exists():
        raise SystemExit('存在项目进程或硬件锁，停止；请核对，勿重复启动或直接删锁。')
    command = [sys.executable, '-B', str(root / 'sorting_demo/start_service.py'), '--enable-ros-planner']
    if request['real']:
        command.append('--enable-real-control')
    print('本次启动命令：', command, flush=True)
    subprocess.run(command, check=True)
elif request['start']:
    print('8080 已开放，仅核对现有服务，不重复启动。', flush=True)
if listening or request['start']:
    def get(path):
        with urlopen('http://192.168.137.100:8080' + path, timeout=8) as response:
            return json.load(response)
    control, camera, teaching = get('/control/status'), get('/status'), get('/teaching/status')
    print('控制模式：', control['mode'], '；任务忙碌：', control['busy'], flush=True)
    print('摄像头：', camera, flush=True)
    print('示教模式：', teaching['setup'].get('task_mode'), teaching['setup'].get('mapping_mode'),
          '；资料检查：', teaching['readiness']['ready'], teaching['readiness'].get('issues'), flush=True)
    if request['start'] and control['mode'] != ('real' if request['real'] else 'simulation'):
        raise SystemExit('现有服务模式与请求不同；未停止、重启或修改服务。')
    if not camera['connected']:
        raise SystemExit('网页已运行，但摄像头尚未就绪，请检查设备上的摄像头连接。')
    print('网页：http://192.168.137.100:8080 。执行运动仍需网页当次确认。', flush=True)
else:
    print('服务尚未启动。可使用 --start；实机网页还须显式添加 --real。', flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true', help='启动网页及 ROS2 纯计算服务；已运行则核对')
    parser.add_argument('--real', action='store_true', help='明确启用网页实机模式；启动本身不运动')
    args = parser.parse_args()
    for name in ('AGENTS.md', '机械臂操作准则.md'):
        content = (ROOT / name).read_bytes()
        if not content:
            raise SystemExit('规则文件为空：' + name)
        print('本次核对本地规则', name, hashlib.sha256(content).hexdigest(), flush=True)
    sources = json.loads((ROOT / 'mac_tools/reviewed_sources.json').read_text())
    request = json.dumps(dict(sources=sources, start=args.start, real=args.real))
    program = REMOTE.replace('REQUEST_JSON', repr(request))
    command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
               '-o', 'StrictHostKeyChecking=yes', 'root@192.168.137.100',
               '/root/miniconda3/envs/ros-arm-env/bin/python -B -']
    try:
        result = subprocess.run(command, input=program, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit('检查或启动超时，未重试。服务可能已启动，请查询状态后再操作。')
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
