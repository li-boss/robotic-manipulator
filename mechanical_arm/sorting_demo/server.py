"""摄像头及示教看板；默认模拟，手动回车/确定授权该次单关节调整。"""
import argparse
import importlib.util
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

from core import EventStore, detect, simulate, statistics, validate_config
from control import Control
from auto_task import AutoTask
from teaching_task import Teaching
from teaching_cards import TeachingCards
from manipulation_task import Manipulation
from manipulation_geometry import ASSETS

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("camera_preview_service", ROOT.parent / "camera_preview/server.py")
camera_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera_module)
Camera, handler_for = camera_module.Camera, camera_module.handler_for


class Demo:
    def __init__(self, camera, config_path=ROOT / "config.json", arm_path=ROOT.parent / "arm_playground/config.json",
                 log_path=ROOT / "logs/events.jsonl", real_control=False):
        self.camera = camera
        self.config_path, self.arm_path = Path(config_path), Path(arm_path)
        self.config = validate_config(json.loads(self.config_path.read_text(encoding="utf-8-sig")))
        self.store = EventStore(log_path)
        self.lock = threading.RLock()
        self.cached = None
        self.control = Control(self.arm_path, real=real_control)
        self.auto = AutoTask(self)
        self.teaching = Teaching(self)
        self.cards = TeachingCards(self)
        self.manipulation = Manipulation(self, self.auto.planner)

    def detection(self):
        import cv2
        import numpy as np
        with self.lock:
            with self.camera.condition:
                if not self.camera.status()["connected"]:
                    self.cached = None
                    return dict(result="camera_unavailable", reason=self.camera.status()["message"])
                jpeg, sequence = self.camera.jpeg, self.camera.sequence
                captured_at = int((time.time() - (time.monotonic() - self.camera.updated)) * 1000)
            if self.cached and self.cached["frame_id"] == sequence:
                return self.cached
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return dict(result="camera_unavailable", reason="图像解码失败")
            self.cached = detect(frame, self.config, sequence, captured_at)
            return self.cached

    def settings(self, fields):
        with self.lock:
            allowed = {"roi", "vision_reviewed", "min_area", "min_color_fraction", "colors", "color_targets"}
            if not isinstance(fields, dict) or set(fields) - allowed:
                raise ValueError("只允许修改视觉参数和颜色映射")
            updated = validate_config(dict(self.config, **fields))
            temporary = self.config_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.config_path)
            self.config = updated
            self.cached = None
            return updated


def demo_handler(demo):
    base = handler_for(demo.camera)

    class Handler(base):
        def send_json(self, status, value):
            try:
                self.reply(status, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass  # Client timeout never retries the completed server action.

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    self.reply(200, "text/html; charset=utf-8", (ROOT / "index.html").read_bytes())
                elif path == '/calibration':
                    self.reply(200,'text/html; charset=utf-8',(ROOT/'calibration.html').read_bytes())
                elif path == '/calibration_ui.js':
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/'calibration_ui.js').read_bytes())
                elif path == '/calibration/status':
                    self.send_json(200,demo.manipulation.calibration.status())
                elif path == '/calibration/export':
                    self.send_json(200,demo.manipulation.calibration.export())
                elif path == '/calibration/live.jpg':
                    self.reply(200,'image/jpeg',demo.manipulation.calibration.live_image())
                elif path.startswith('/calibration/frame/') and path.endswith('.jpg'):
                    self.reply(200,'image/jpeg',demo.manipulation.calibration.image(path[len('/calibration/frame/'):-4]))
                elif path == '/manipulation':
                    self.reply(200,'text/html; charset=utf-8',(ROOT/'manipulation.html').read_bytes())
                elif path in ('/manipulation_ui.js','/manipulation_view.js'):
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/path[1:]).read_bytes())
                elif path == '/manipulation/status':
                    self.send_json(200,demo.manipulation.status())
                elif path == '/manipulation/acceptance':
                    self.send_json(200,demo.manipulation.acceptance())
                elif path == '/manipulation/robot.urdf':
                    from manipulation_geometry import robot_description
                    self.reply(200,'application/xml; charset=utf-8',robot_description(demo.manipulation.load(),demo.control.config()))
                elif path.startswith(('/manipulation/assets/','/manipulation/meshes/')):
                    name=path.rsplit('/',1)[-1]
                    if name not in {'base_link.STL','link1.STL','link2.STL','link3.STL','link4.STL','link5.STL'}:
                        raise ValueError('无效模型资源名称')
                    self.reply(200,'application/octet-stream',(ASSETS/'meshes'/name).read_bytes())
                elif path == '/teaching-guide':
                    self.reply(200, 'text/plain; charset=utf-8', (ROOT / '无标记引导示教说明.md').read_bytes())
                elif path == '/auto_ui.js':
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/'auto_ui.js').read_bytes())
                elif path == '/teaching_ui.js':
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/'teaching_ui.js').read_bytes())
                elif path == '/state_ui.js':
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/'state_ui.js').read_bytes())
                elif path == '/cards_ui.js':
                    self.reply(200,'text/javascript; charset=utf-8',(ROOT/'cards_ui.js').read_bytes())
                elif path == '/teaching/cards/status':
                    self.send_json(200,demo.cards.status())
                elif path.startswith('/teaching/photos/'):
                    self.reply(200,'image/jpeg',demo.cards.photo(path[len('/teaching/photos/'):]))
                elif path == "/detection":
                    self.send_json(200, demo.detection())
                elif path == "/settings":
                    with demo.lock:
                        self.send_json(200, demo.config)
                elif path == "/control/status":
                    self.send_json(200, demo.control.status())
                elif path == '/auto/status':
                    self.send_json(200,demo.auto.status())
                elif path == '/teaching/status':
                    self.send_json(200,demo.teaching.status())
                elif path in ("/events", "/statistics", "/task-status"):
                    events, errors = demo.store.latest()
                    if path == "/events":
                        self.send_json(200, dict(events=events[-100:][::-1], malformed_lines=errors))
                    elif path == "/statistics":
                        self.send_json(200, statistics(events))
                    else:
                        latest = events[-1] if events else None
                        self.send_json(200, dict(latest=latest, real_control_available=demo.control.real,
                            message="手动确定即执行；自动任务仍需完整预览确认；实机结果由人工报告"))
                else:
                    super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.send_json(503, dict(error=str(exc)))

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                # 控制入口额外要求同源Origin及不可跨站读取的令牌。
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    self.send_json(403, dict(error="拒绝跨站请求"))
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("须使用application/json")
                size = int(self.headers.get("Content-Length", "0"))
                limit=262144 if path.startswith('/manipulation/') else 16384
                if not 0 < size <= limit:
                    raise ValueError(f"请求大小须为1～{limit}字节")
                fields = json.loads(self.rfile.read(size))
                if not isinstance(fields, dict):
                    raise ValueError("请求必须是JSON对象")
                if path.startswith(('/control/','/auto/','/teaching/','/manipulation/','/calibration/')):
                    if not origin or urlsplit(origin).scheme != 'http' or fields.get('csrf') != demo.control.csrf:
                        self.send_json(403, dict(error='控制请求必须来自本页面并携带有效令牌'))
                        return
                    actions = {'/control/read': demo.control.read,
                              '/control/move': lambda: demo.control.direct_move(fields),
                              '/control/states/analyze': lambda: demo.control.analyze_state(fields),
                              '/control/states/save': lambda: demo.control.save_state(fields),
                              '/control/states/delete': lambda: demo.control.delete_state(fields),
                              '/control/states/execute': lambda: demo.control.state_move(fields),
                              '/control/preview': lambda: demo.control.preview(fields),
                              '/control/execute': lambda: demo.control.execute(fields)}
                    if path.startswith('/calibration/'):
                        calibration = demo.manipulation.calibration
                        actions.update({
                            '/calibration/session': lambda: calibration.new_session(fields),
                            '/calibration/capture': lambda: calibration.capture(fields),
                            '/calibration/add': lambda: calibration.add(fields),
                            '/calibration/remove': lambda: calibration.remove(fields),
                            '/calibration/solve': lambda: calibration.solve(fields),
                            '/calibration/apply': lambda: calibration.apply(fields),
                            '/calibration/table-height': lambda: calibration.set_table_height(fields),
                            '/calibration/exclude-point': lambda: calibration.exclude_point(fields),
                            '/calibration/adopt-position': lambda: calibration.adopt_position(fields),
                            '/calibration/check': lambda: calibration.point_check(fields)})
                    if path.startswith('/manipulation/'):
                        actions.update({
                            '/manipulation/settings': lambda: demo.manipulation.save(fields),
                            '/manipulation/prepare-cad': lambda: demo.manipulation.prepare_cad(fields),
                            '/manipulation/capture': lambda: demo.manipulation.capture(fields),
                            '/manipulation/intrinsics': lambda: demo.manipulation.intrinsics(fields),
                            '/manipulation/anchor': lambda: demo.manipulation.anchor(fields),
                            '/manipulation/target': lambda: dict(target=demo.manipulation.target(demo.manipulation.load(),fields['target'])[0]),
                            '/manipulation/destination': lambda: demo.manipulation.destination(fields),
                            '/manipulation/preview': lambda: demo.manipulation.preview(fields),
                            '/manipulation/step': lambda: demo.manipulation.step(fields),
                            '/manipulation/validation': lambda: demo.manipulation.validation(fields),
                            '/manipulation/invalidate': demo.manipulation.invalidate,
                            '/manipulation/result': lambda: demo.manipulation.result(fields)})
                    if path.startswith('/auto/'):
                        actions.update({'/auto/settings': lambda: demo.auto.save(fields),
                              '/auto/capture': demo.auto.capture,
                              '/auto/anchor': lambda: demo.auto.anchor(fields),
                              '/auto/intrinsics': lambda: demo.auto.intrinsics(fields),
                              '/auto/dryrun': lambda: demo.auto.preview(True),
                              '/auto/preview': demo.auto.preview,
                              '/auto/result': lambda: demo.auto.result(fields)})
                    if path.startswith('/teaching/'):
                        actions.update({'/teaching/capture':lambda:demo.teaching.capture(fields),
                            '/teaching/samples':lambda:demo.teaching.samples(fields),
                            '/teaching/fit':lambda:demo.teaching.fit(fields),
                            '/teaching/validation':lambda:demo.teaching.validation(fields),
                            '/teaching/preview':lambda:demo.teaching.preview(fields)})
                    if path.startswith('/teaching/cards/'):
                        actions.update({'/teaching/cards/fit':lambda:demo.cards.fit_reported(fields),
                            '/teaching/cards/record':lambda:demo.cards.record(fields),
                            '/teaching/cards/angles':lambda:demo.cards.angles(fields),
                            '/teaching/cards/import':lambda:demo.cards.import_frames(fields),
                            '/teaching/cards/confirm':lambda:demo.cards.confirm(fields),
                            '/teaching/cards/preview':lambda:demo.cards.preview(fields)})
                    action = actions.get(path)
                    if action is None:
                        raise ValueError('未知控制接口')
                    self.send_json(200, action())
                elif path == "/settings":
                    self.send_json(200, demo.settings(fields))
                elif path == "/simulate":
                    with demo.lock:
                        detection = demo.cached
                        if not detection or fields.get("request_id") != detection["request_id"]:
                            raise ValueError("检测已更新，请重新识别后生成计划")
                        if not demo.camera.status()["connected"]:
                            raise ValueError("摄像头未就绪")
                        arm_config = json.loads(demo.arm_path.read_text(encoding="utf-8-sig"))
                        self.send_json(200, simulate(detection, demo.config, arm_config, demo.store))
                else:
                    self.send_json(404, dict(error="无此接口"))
            except (ValueError, KeyError) as exc:
                self.send_json(400, dict(error=str(exc)))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.send_json(503, dict(error=str(exc)))
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.137.100")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--enable-real-control", action="store_true", help="启用逐次确认的实机单关节示教；启动不运动")
    args = parser.parse_args()
    camera = Camera(args.device)
    demo = Demo(camera, real_control=args.enable_real_control)
    server = ThreadingHTTPServer((args.host, args.port), demo_handler(demo))
    threading.Thread(target=camera.capture, daemon=True).start()
    demo.control.audit(dict(operation='startup', arguments=vars(args)))
    print(f"颜色积木演示 http://{args.host}:{args.port}；控制模式={'实机' if args.enable_real_control else '模拟'}；启动不运动", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop.set()
        with camera.condition:
            camera.condition.notify_all()
        server.server_close()


if __name__ == "__main__":
    main()
