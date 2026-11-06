"""在开发板上启动网页服务（及可选 ROS2 纯计算规划节点）。启动不读取舵机、不运动。

用法（开发板上，项目放在 /root/jjt）：
    /root/miniconda3/envs/ros-arm-env/bin/python -B /root/jjt/board/start_service.py --enable-real-control --enable-ros-planner
不加 --enable-real-control 为模拟模式。
"""
import argparse
import hashlib
import socket
import subprocess
import sys
import time

from paths import BOARD, DATA, MODELS, RULE_FILES, WEB

HOST, PORT = '192.168.137.100', 8080

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--enable-real-control', action='store_true')
parser.add_argument('--enable-ros-planner', action='store_true', help='启动仅计算的ROS2节点；不会驱动硬件')
parser.add_argument('--host', default=HOST)
parser.add_argument('--no-pc-planner', action='store_true', help='不请求电脑端规划（默认先试 192.168.137.101:8095，连不上自动本地计算）')
args = parser.parse_args()

for path in RULE_FILES:
    if not path.is_file():
        raise SystemExit(f'缺少规则文件：{path}')
checked = [*RULE_FILES, *sorted(BOARD.glob('*.py')), *sorted(WEB.glob('*')),
           MODELS / 'manipulation/cad_profile.json', MODELS / 'manipulation/manifest.json']
for path in checked:
    print('本次核对', path.name, hashlib.sha256(path.read_bytes()).hexdigest()[:16], flush=True)
print('实际启动参数', vars(args), '；启动不会读取舵机或运动', flush=True)
with socket.socket() as probe:
    if probe.connect_ex((args.host, PORT)) == 0:
        raise SystemExit(f'{PORT}已被占用，未启动第二个服务')
logs = DATA / 'logs'
logs.mkdir(parents=True, exist_ok=True)
environment = None
planner = None
if args.enable_ros_planner:
    raw = subprocess.check_output(['/bin/bash', '-c', 'source /opt/ros/humble/setup.bash && env -0'])
    environment = dict(item.decode().split('=', 1) for item in raw.split(b'\0') if item)
    # 逆解只算小矩阵；开发板上多线程 BLAS 反而更慢。
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        environment[name] = '1'
    check = subprocess.run([sys.executable, '-B', '-c', 'import rclpy, std_msgs.msg, cv2, numpy'], env=environment)
    if check.returncode != 0:
        raise SystemExit('ROS2与数值依赖不兼容，未启动任何服务')
    with (logs / 'planner.log').open('ab') as planner_log:
        planner = subprocess.Popen([sys.executable, '-u', '-B', str(BOARD / 'ros_planner.py')], cwd=BOARD,
                                   stdin=subprocess.DEVNULL, stdout=planner_log, stderr=planner_log,
                                   start_new_session=True, env=environment)
    time.sleep(.5)
    if planner.poll() is not None:
        raise SystemExit('ROS2纯计算节点启动失败，请检查 data/logs/planner.log')
    (DATA / 'planner.pid').write_text(str(planner.pid) + '\n')
log_path = logs / 'server.log'
with log_path.open('ab') as log:
    command = [sys.executable, '-u', '-B', str(BOARD / 'server.py'), '--host', args.host, '--port', str(PORT),
               '--device', '/dev/video0']
    if args.enable_real_control:
        command.append('--enable-real-control')
    if not args.enable_ros_planner:
        command.append('--no-ros-planner')
    if args.no_pc_planner:
        command.append('--no-pc-planner')
    child = subprocess.Popen(command, cwd=BOARD, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, env=environment)
    for _ in range(300):
        if child.poll() is not None:
            break
        with socket.socket() as probe:
            if probe.connect_ex((args.host, PORT)) == 0:
                break
        time.sleep(.1)
    else:
        child.terminate()
        if planner is not None:
            planner.terminate()
        raise SystemExit('网页端口30秒内未就绪，已停止本次启动进程')
    if child.poll() is not None:
        if planner is not None:
            planner.terminate()
        raise SystemExit(f'服务启动失败：退出码{child.returncode}，检查{log_path}')
    (DATA / 'server.pid').write_text(str(child.pid) + '\n')
    print(f'网页服务PID={child.pid}；日志={log_path}；启动不运动，实机启用={args.enable_real_control}')
