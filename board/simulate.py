"""本地全模拟服务：合成相机 + 假舵机，不导入 Arm_Lib、不连接任何设备。

给电脑端（方块识别 / 手势识别）同学联调用：在自己电脑上运行
    python board/simulate.py            # 默认 http://127.0.0.1:8092
然后让 pc/ 下的程序连 --board http://127.0.0.1:8092 。网页同样可打开（/modes 开启模式）。

画面：桌面上一块黄色 30mm 方块，按假舵机当前关节角从 CAD 相机模型渲染；
抓放完成后方块会“移动”到放置点。所有配置都是合成数据，只放在临时目录，禁止部署到设备。
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

BOARD = Path(__file__).resolve().parent
sys.path.insert(0, str(BOARD / 'tests'))


class SimFake:
    """有状态的假舵机：写入即到位，读数返回最近目标。仅模拟模式。"""
    values = []
    calls = []
    max_delay_ms = 400

    def __init__(self, config, real=False):
        if real:
            raise AssertionError('模拟服务禁止实机')
        self.config, self.real = config, False

    def read_report(self):
        return [dict(id=i, angle=v, read_ok=True, error=None) for i, v in enumerate(self.values, 1)]

    def write(self, pairs, hold_ms=None, retries=0):
        import arm
        pairs = [(i, arm.angle(self.config, i, v)) for i, v in pairs]
        duration, settle = arm.timing(self.config)
        self.last_retries = []
        type(self).calls.append(pairs)
        time.sleep(min(duration + settle if hold_ms is None else hold_ms, self.max_delay_ms) / 1000)   # 加速但保留节奏
        for i, v in pairs:
            self.values[i - 1] = v


class SyntheticCamera:
    """按当前关节角渲染方块的合成相机；接口与 camera.Camera 相同（condition / jpeg / status）。"""

    def __init__(self, cfg=None, angles=None):
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.sequence = 1
        self.device = 'SYNTHETIC ONLY'
        self.size = [640, 480]
        self.cfg, self.angles = cfg, angles
        self.block_xy = [0.0, 260.0]
        self.block_yaw = 0.0
        self.jpeg = self._render(None)
        self.updated = time.monotonic()

    def _render(self, angles):
        image = np.full((480, 640, 3), (44, 37, 29), np.uint8)
        for x in range(0, 640, 40):
            cv2.line(image, (x, 0), (x, 479), (70, 62, 50), 1)
        for y in range(0, 480, 40):
            cv2.line(image, (0, y), (639, y), (70, 62, 50), 1)
        if self.cfg is None or angles is None:
            cv2.rectangle(image, (299, 295), (341, 337), (40, 210, 245), -1)
        else:
            from manipulation_geometry import camera_pose
            k, d, r, t = camera_pose(self.cfg, angles[:5])
            z = self.cfg['table_z_mm'] + 30   # 方块顶面
            a = np.radians(self.block_yaw)
            rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            corners = np.array([[-15, -15], [15, -15], [15, 15], [-15, 15]]) @ rot.T + self.block_xy
            world = np.c_[corners, np.full(4, z)].astype(float)
            if np.all((r @ world.T + t[:, None])[2] > 0):
                px = cv2.projectPoints(world, cv2.Rodrigues(r)[0], t, k, d)[0].reshape(-1, 2)
                cv2.fillConvexPoly(image, np.rint(px).astype(np.int32), (40, 210, 245))
        cv2.putText(image, 'SIMULATION - NO HARDWARE', (20, 35), cv2.FONT_HERSHEY_SIMPLEX, .7, (210, 225, 240), 1)
        return cv2.imencode('.jpg', image)[1].tobytes()

    def refresh(self):
        """按当前关节角重新渲染一帧并通知推流。"""
        image = self._render(self.angles() if self.angles else None)
        with self.condition:
            self.jpeg = image
            self.sequence += 1
            self.updated = time.monotonic()
            self.condition.notify_all()

    def capture(self):
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(0.2)

    def status(self):
        with self.condition:
            self.updated = time.monotonic()
            return dict(connected=True, device=self.device, resolution=self.size, message='合成画面（模拟）')


def application(root, pc_planner=None):
    """在 root 临时目录里搭一套完整的合成配置并返回 Demo。

    pc_planner 给出时同时接入合成九点参考（仅测试用），使抓放走“电脑端计算 + 开发板复核”。"""
    from test_dynamic_manipulation import dynamic_case
    from server import Demo
    from dance import DEFAULT_DANCES
    start = [90, 80, 35, 10, 90, 90]          # 合成起始姿态（随臂模式：任意静止姿态均可冻结）
    cfg, arms, _, _ = dynamic_case(start)
    cfg['destinations'] = {'模拟放置点': [-260 * np.sin(np.radians(15)), 260 * np.cos(np.radians(15))]}
    data = root / 'data'
    data.mkdir(parents=True)
    (data / 'arm_config.json').write_text(json.dumps(arms), encoding='utf-8')
    (data / 'dances.json').write_text(json.dumps(DEFAULT_DANCES, ensure_ascii=False, indent=2), encoding='utf-8')
    # 跳舞起始姿态：竖直（课程舞蹈要求六个关节都是 90°，画圈也可用）；另一个姿态用于演示“不匹配”提示。
    base = dict(id='0' * 32, name='模拟跳舞起点（竖直）', angles=[90, 90, 90, 90, 90, 90], order=[6, 5, 4, 3, 2, 1],
                motion_ms=1000, teaching_stage='', updated_at='synthetic', path_verified=False)
    other = dict(id='1' * 32, name='模拟其他姿态', angles=[90, 100, 60, 60, 130, 90], order=[6, 1, 2, 3, 4, 5], motion_ms=1000,
                 teaching_stage='', updated_at='synthetic', path_verified=False)
    (data / 'manual_states.json').write_text(json.dumps(dict(version=1, states=[base, other]), ensure_ascii=False), encoding='utf-8')
    SimFake.values, SimFake.calls = start.copy(), []
    camera = SyntheticCamera()
    demo = Demo(camera, data_dir=data, real_control=False, planner=None)
    demo.control.factory = SimFake
    captured = demo.manipulation.capture({})
    cfg['cad']['anchor_frame'] = captured['id']
    if pc_planner is not None:
        cfg['max_position_error_mm'] = 8.0     # 与开发板现场抓放配置相同的逆解位置容差
    demo.manipulation.path.write_text(json.dumps(cfg), encoding='utf-8')
    demo.control.extra_audit_paths.append(demo.manipulation.path)
    if pc_planner is not None:
        from types import SimpleNamespace
        from test_manipulation_guided import teaching_fixture
        teaching = teaching_fixture(cfg)
        demo.nine_point = SimpleNamespace(load=lambda: json.loads(json.dumps(teaching)), bound=lambda setup: None)
        demo.manipulation.pc_planner = pc_planner
    camera.cfg, camera.angles = cfg, (lambda: list(SimFake.values))
    camera.refresh()
    # 抓放成功后把方块挪到放置点，方便连续联调。
    original = demo.manipulation.run

    def run(controller, p):
        result = original(controller, p)
        target = p['parameters']['plan']['target']
        if target.get('task_mode') == 'pick_place':
            camera.block_xy = list(target['destination_xy_mm'])
        return result
    demo.manipulation.run = run
    return demo


def main():
    from server import demo_handler
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8092)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--pc-planner', metavar='URL', help='联调电脑端规划服务，例如 http://127.0.0.1:8095（同时启用合成九点参考）')
    args = parser.parse_args()
    planner = None
    if args.pc_planner:
        from pc_planner import PcPlanner
        planner = PcPlanner(args.pc_planner)
    with tempfile.TemporaryDirectory(prefix='arm_simulation_') as folder:
        demo = application(Path(folder), planner)
        handler = demo_handler(demo)
        handler.log_message = lambda self, *a: None
        server = ThreadingHTTPServer((args.host, args.port), handler)
        threading.Thread(target=demo.camera.capture, daemon=True).start()
        print(f'全模拟服务（无硬件）：http://{args.host}:{args.port}/   开启模式：/modes', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            demo.camera.stop.set()
            server.server_close()


if __name__ == '__main__':
    sys.path.insert(0, str(BOARD))
    main()
