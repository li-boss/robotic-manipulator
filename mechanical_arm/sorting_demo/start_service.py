"""启动摄像头及示教服务；不连接机械臂总线、不发送运动。"""
import argparse
import hashlib
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--enable-real-control', action='store_true')
parser.add_argument('--enable-ros-planner', action='store_true', help='启动仅计算的ROS2节点；不会驱动硬件')
args = parser.parse_args()
if ROOT != Path('/root/jjt/sorting_demo'):
    raise SystemExit('此启动器只用于/root/jjt/sorting_demo')
for name in ('AGENTS.md', '机械臂操作准则.md'):
    path = ROOT.parent / name
    if not path.is_file():
        raise SystemExit(f'缺少规则文件：{path}')
for path in [ROOT.parent/'AGENTS.md',ROOT.parent/'机械臂操作准则.md',Path(__file__),
             ROOT/'server.py',ROOT/'control.py',ROOT/'auto_task.py',ROOT/'auto_plan.py',ROOT/'auto_geometry.py',ROOT/'ros_planner.py',
             ROOT/'teaching_task.py',ROOT/'teaching_plan.py',ROOT/'teaching_vision.py',ROOT/'teaching_cards.py',ROOT/'cards_ui.js',
             ROOT/'manipulation_task.py',ROOT/'manipulation_geometry.py',ROOT/'manipulation.html',
             ROOT/'manipulation_reference.py',ROOT/'manipulation_ik.py',ROOT/'manipulation_guided.py',ROOT/'manipulation_collision.py',ROOT/'planning_worker.py',
             ROOT/'manipulation_ui.js',ROOT/'manipulation_view.js',ROOT/'cad_geometry.py',
             ROOT/'models/manipulation/cad_profile.json',ROOT/'models/manipulation/manifest.json']:
    print('本次核对',path.name,hashlib.sha256(path.read_bytes()).hexdigest()[:16],flush=True)
print('实际启动参数',vars(args),'；启动不会读取舵机或运动',flush=True)
with socket.socket() as probe:
    if probe.connect_ex(('192.168.137.100', 8080)) == 0:
        raise SystemExit('8080已被占用，未启动第二个服务')
log_path = ROOT / 'logs/server.log'
log_path.parent.mkdir(exist_ok=True)
environment=None
planner=None
if args.enable_ros_planner:
    raw=subprocess.check_output(['/bin/bash','-c','source /opt/ros/humble/setup.bash && env -0'])
    environment=dict(item.decode().split('=',1) for item in raw.split(b'\0') if item)
    # IK solves tiny matrices; threaded BLAS adds large overhead on the board.
    for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
        environment[name]='1'
    check=subprocess.run([sys.executable,'-B','-c','import rclpy, std_msgs.msg, cv2, numpy'],env=environment)
    if check.returncode!=0:raise SystemExit('ROS2与数值依赖不兼容，未启动任何服务')
    with (ROOT/'logs/planner.log').open('ab') as planner_log:
        planner=subprocess.Popen([sys.executable,'-u','-B',str(ROOT/'ros_planner.py')],cwd=ROOT,
             stdin=subprocess.DEVNULL,stdout=planner_log,stderr=planner_log,start_new_session=True,env=environment)
    time.sleep(.5)
    if planner.poll() is not None:raise SystemExit('ROS2纯计算节点启动失败，请检查logs/planner.log')
    (ROOT/'planner.pid').write_text(str(planner.pid)+'\n')
with log_path.open('ab') as log:
    command = [sys.executable, '-u', '-B', str(ROOT / 'server.py'),
               '--host', '192.168.137.100', '--port', '8080', '--device', '/dev/video0']
    if args.enable_real_control:
        command.append('--enable-real-control')
    child = subprocess.Popen(command,
                             cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True,env=environment)
    for _ in range(300):
        if child.poll() is not None:break
        with socket.socket() as probe:
            if probe.connect_ex(('192.168.137.100',8080))==0:break
        time.sleep(.1)
    else:
        child.terminate()
        if planner is not None:planner.terminate()
        raise SystemExit('网页端口30秒内未就绪，已停止本次启动进程')
    if child.poll() is not None:
        if planner is not None:planner.terminate()
        raise SystemExit(f'服务启动失败：退出码{child.returncode}，检查{log_path}')
    (ROOT / 'server.pid').write_text(str(child.pid) + '\n')
    print(f'摄像头服务PID={child.pid}；日志={log_path}；启动不运动，实机启用={args.enable_real_control}')
