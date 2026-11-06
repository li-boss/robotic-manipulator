"""开发板网页服务：摄像头、手动调整、人工框选抓放、相机标定，以及给电脑端识别模块用的 /api/v1。

默认模拟；只有 --enable-real-control 才允许实机下发。启动本身不读取舵机、不运动。

路由分三类：
  1. 网页与静态资源（GET）：/、/manipulation、/calibration、/modes 及其 js。
  2. 网页控制接口（POST，要求同源 Origin + csrf 令牌）：/control/* /manipulation/* /calibration/* /modes/*。
  3. 电脑端接口（/api/v1/*，见 docs/接口与合并指南.md）：只有用户在网页上开启模式后才接受运动相关请求。
"""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from camera import Camera, handler_for
from control import Control
from events import EventStore
from manipulation_task import Manipulation
from manipulation_geometry import ASSETS
from modes import Modes
from nine_point_source import NinePointSource
from paths import DATA, WEB

PAGES = {'/': 'index.html', '/manipulation': 'manipulation.html', '/calibration': 'calibration.html', '/modes': 'modes.html'}
SCRIPTS = {'home_ui.js', 'manipulation_ui.js', 'manipulation_view.js', 'calibration_ui.js', 'modes_ui.js'}
MESHES = {'base_link.STL', 'link1.STL', 'link2.STL', 'link3.STL', 'link4.STL', 'link5.STL'}


class Demo:
    """把各业务模块装配在一起。所有数据文件都在 data_dir（默认 board/data）。"""

    def __init__(self, camera, data_dir=DATA, real_control=False, planner=None, pc_planner=None):
        self.camera = camera
        self.data_dir = Path(data_dir)
        self.store = EventStore(self.data_dir / 'logs/events.jsonl')
        self.control = Control(self.data_dir / 'arm_config.json', real=real_control)
        self.nine_point = NinePointSource(self.control, self.data_dir)
        self.manipulation = Manipulation(self, planner, pc_planner)
        self.modes = Modes(self)


def demo_handler(demo):
    base = handler_for(demo.camera)
    calibration = demo.manipulation.calibration

    def get_routes():
        return {
            '/control/status': lambda: demo.control.status(),
            '/manipulation/status': lambda: demo.manipulation.status(),
            '/manipulation/acceptance': lambda: demo.manipulation.acceptance(),
            '/calibration/status': lambda: calibration.status(),
            '/calibration/export': lambda: calibration.export(),
            '/modes/status': lambda: demo.modes.status(),
            # 电脑端只读接口
            '/api/v1/health': lambda: demo.modes.api_health(),
            '/api/v1/mode': lambda: demo.modes.api_mode(),
            '/api/v1/job': lambda: demo.modes.api_job(),
        }

    def page_actions(fields):
        """网页控制接口：必须同源且带 csrf。"""
        return {
            '/control/read': lambda: demo.control.read(),
            '/control/move': lambda: demo.control.direct_move(fields),
            '/control/execute': lambda: demo.control.execute(fields),
            '/control/states/analyze': lambda: demo.control.analyze_state(fields),
            '/control/states/save': lambda: demo.control.save_state(fields),
            '/control/states/delete': lambda: demo.control.delete_state(fields),
            '/control/states/execute': lambda: demo.control.state_move(fields),
            '/manipulation/settings': lambda: demo.manipulation.save(fields),
            '/manipulation/prepare-cad': lambda: demo.manipulation.prepare_cad(fields),
            '/manipulation/capture': lambda: demo.manipulation.capture(fields),
            '/manipulation/intrinsics': lambda: demo.manipulation.intrinsics(fields),
            '/manipulation/anchor': lambda: demo.manipulation.anchor(fields),
            '/manipulation/target': lambda: dict(target=demo.manipulation.target(demo.manipulation.load(), fields['target'])[0]),
            '/manipulation/destination': lambda: demo.manipulation.destination(fields),
            '/manipulation/preview': lambda: demo.manipulation.preview(fields),
            '/manipulation/step': lambda: demo.manipulation.step(fields),
            '/manipulation/validation': lambda: demo.manipulation.validation(fields),
            '/manipulation/invalidate': lambda: demo.manipulation.invalidate(),
            '/manipulation/result': lambda: demo.manipulation.result(fields),
            '/calibration/session': lambda: calibration.new_session(fields),
            '/calibration/capture': lambda: calibration.capture(fields),
            '/calibration/add': lambda: calibration.add(fields),
            '/calibration/remove': lambda: calibration.remove(fields),
            '/calibration/solve': lambda: calibration.solve(fields),
            '/calibration/apply': lambda: calibration.apply(fields),
            '/calibration/table-height': lambda: calibration.set_table_height(fields),
            '/calibration/exclude-point': lambda: calibration.exclude_point(fields),
            '/calibration/adopt-position': lambda: calibration.adopt_position(fields),
            '/calibration/check': lambda: calibration.point_check(fields),
            '/modes/preview': lambda: demo.modes.preview(fields),
            '/modes/arm': lambda: demo.modes.arm(fields),
            '/modes/disarm': lambda: demo.modes.disarm(fields, source='web'),
            '/modes/dance/presets': lambda: demo.modes.dance_presets(),
            '/modes/dance/preview': lambda: demo.modes.dance_preview(fields),
            '/modes/dance/start': lambda: demo.modes.dance_start(fields),
            '/modes/dance/stop': lambda: demo.modes.dance_stop(fields),
        }

    def api_actions(fields):
        """电脑端接口：不需要 csrf，但运动相关请求只在网页已开启对应模式时有效。"""
        return {
            '/api/v1/block/frame': lambda: demo.modes.api_block_frame(fields),
            '/api/v1/block/target': lambda: demo.modes.api_block_target(fields),
            '/api/v1/gesture': lambda: demo.modes.api_gesture(fields),
            '/api/v1/stop': lambda: demo.modes.disarm(fields, source='pc'),
        }

    class Handler(base):
        def send_json(self, status, value):
            try:
                self.reply(status, 'application/json; charset=utf-8', json.dumps(value, ensure_ascii=False).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass  # 客户端超时不会让服务端重做已完成的动作。

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                routes = get_routes()
                if path in PAGES:
                    self.reply(200, 'text/html; charset=utf-8', (WEB / PAGES[path]).read_bytes())
                elif path.lstrip('/') in SCRIPTS:
                    self.reply(200, 'text/javascript; charset=utf-8', (WEB / path.lstrip('/')).read_bytes())
                elif path in routes:
                    self.send_json(200, routes[path]())
                elif path == '/api/v1/frame.jpg':
                    self.reply(200, 'image/jpeg', demo.modes.api_snapshot())
                elif path == '/calibration/live.jpg':
                    self.reply(200, 'image/jpeg', calibration.live_image())
                elif path.startswith('/calibration/frame/') and path.endswith('.jpg'):
                    self.reply(200, 'image/jpeg', calibration.image(path[len('/calibration/frame/'):-4]))
                elif path == '/manipulation/robot.urdf':
                    from manipulation_geometry import robot_description
                    self.reply(200, 'application/xml; charset=utf-8',
                               robot_description(demo.manipulation.load(), demo.control.config()))
                elif path.startswith(('/manipulation/assets/', '/manipulation/meshes/')):
                    name = path.rsplit('/', 1)[-1]
                    if name not in MESHES:
                        raise ValueError('无效模型资源名称')
                    self.reply(200, 'application/octet-stream', (ASSETS / 'meshes' / name).read_bytes())
                else:
                    super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.send_json(503, dict(error=str(exc)))

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    self.send_json(403, dict(error='拒绝跨站请求'))
                    return
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('须使用application/json')
                size = int(self.headers.get('Content-Length', '0'))
                limit = 262144 if path.startswith(('/manipulation/', '/api/')) else 16384
                if not 0 < size <= limit:
                    raise ValueError(f'请求大小须为1～{limit}字节')
                fields = json.loads(self.rfile.read(size))
                if not isinstance(fields, dict):
                    raise ValueError('请求必须是JSON对象')
                if path.startswith('/api/v1/'):
                    action = api_actions(fields).get(path)
                else:
                    if not origin or urlsplit(origin).scheme != 'http' or fields.get('csrf') != demo.control.csrf:
                        self.send_json(403, dict(error='控制请求必须来自本页面并携带有效令牌'))
                        return
                    action = page_actions(fields).get(path)
                if action is None:
                    self.send_json(404, dict(error='无此接口'))
                    return
                self.send_json(200, action())
            except (ValueError, KeyError) as exc:
                self.send_json(400, dict(error=str(exc)))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.send_json(503, dict(error=str(exc)))
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.137.100')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--device', default='/dev/video0')
    parser.add_argument('--enable-real-control', action='store_true', help='允许网页确认后实机下发；启动不运动')
    parser.add_argument('--no-ros-planner', action='store_true', help='不做ROS2独立复核（默认要求ROS2纯计算节点）')
    parser.add_argument('--pc-planner', default=None, help='电脑端规划服务地址（默认 http://192.168.137.101:8095）')
    parser.add_argument('--no-pc-planner', action='store_true', help='不请求电脑端规划，只用开发板本地计算')
    args = parser.parse_args()
    from ros_client import RosClient
    from pc_planner import DEFAULT_URL, PcPlanner
    camera = Camera(args.device)
    pc = None if args.no_pc_planner else PcPlanner(args.pc_planner or DEFAULT_URL)
    demo = Demo(camera, real_control=args.enable_real_control, planner=None if args.no_ros_planner else RosClient(),
                pc_planner=pc)
    server = ThreadingHTTPServer((args.host, args.port), demo_handler(demo))
    threading.Thread(target=camera.capture, daemon=True).start()
    demo.control.audit(dict(operation='startup', arguments=vars(args)))
    print(f"机械臂服务 http://{args.host}:{args.port}；控制模式={'实机' if args.enable_real_control else '模拟'}；启动不运动", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop.set()
        with camera.condition:
            camera.condition.notify_all()
        server.server_close()


if __name__ == '__main__':
    main()
