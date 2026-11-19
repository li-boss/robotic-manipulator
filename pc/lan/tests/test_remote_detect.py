"""局域网方块识别（jjt-detect/1）：协议校验、识别服务、Mac 端转发，以及对本地模拟开发板的整条流程。
全部在本机进行，不连接机械臂。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

import cv2
import numpy as np

PC = Path(__file__).resolve().parents[2]
BOARD = PC.parent / 'board'
sys.path[:0] = [str(PC), str(BOARD), str(BOARD / 'tests')]

from armlink import BoardClient  # noqa: E402
from block_vision.color_detector import ColorBlockDetector  # noqa: E402
from lan.detect_protocol import PROTOCOL, ProtocolError, parse_reply  # noqa: E402
from lan.detect_server import DetectService, make_handler  # noqa: E402
from lan.remote_detector import RemoteDetectError, RemoteDetector  # noqa: E402

FRAME = 'ab' * 16
SIZE = (640, 480)
GOOD = dict(corners=[[300, 200], [340, 200], [340, 240], [300, 240]], center=[320, 220], score=.9, label='yellow')


def reply(**extra):
    return dict(dict(protocol=PROTOCOL, frame_id=FRAME, model='m', ms=1.0, detections=[GOOD]), **extra)


def yellow_square_jpeg():
    image = np.full((480, 640, 3), 40, np.uint8)
    cv2.rectangle(image, (290, 190), (350, 250), (0, 220, 230), -1)
    return cv2.imencode('.jpg', image)[1].tobytes()


def serve(handler):
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def fake_service(payload):
    """返回固定 JSON 的假识别服务（模拟写错或被改过的服务）。"""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, value):
            body = json.dumps(value).encode()
            self.send_response(200);self.send_header('Content-Length', str(len(body)));self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(dict(protocol=PROTOCOL, ready=True, model='fake'))

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self._send(dict(payload, frame_id=self.headers['X-Frame-Id']) if 'frame_id' not in payload else payload)
    return serve(Handler)


class ProtocolTests(unittest.TestCase):
    def test_valid_reply_parses_to_detections(self):
        found = parse_reply(reply(), FRAME, SIZE)
        self.assertEqual(found[0].to_dict(), dict(score=.9, corners=GOOD['corners'], center=[320.0, 220.0], label='yellow'))
        bbox = parse_reply(reply(detections=[dict(bbox=[10, 10, 50, 40], angle_deg=12, score=.5)]), FRAME, SIZE)
        self.assertEqual(bbox[0].to_dict()['angle_deg'], 12.0)
        self.assertEqual(parse_reply(reply(detections=[]), FRAME, SIZE), [])

    def test_anything_outside_the_protocol_is_rejected(self):
        bad = [reply(skip_collision_checks=True),                                   # 顶层多余字段
               reply(detections=[dict(GOOD, skip_collision_checks=True)]),          # 检测里多余字段
               reply(detections=[dict(GOOD, joints=[90] * 6)]),                     # 不得夹带关节角
               reply(protocol='jjt-detect/0'), reply(frame_id='cd' * 16),
               reply(detections=[dict(GOOD, corners=GOOD['corners'][:3])]),
               reply(detections=[dict(GOOD, corners=[[640, 0]] + GOOD['corners'][1:])]),   # 出图
               reply(detections=[dict(GOOD, center=[float('nan'), 1])]),
               reply(detections=[dict(GOOD, score=1.5)]), reply(detections=[dict(GOOD, score=True)]),
               reply(detections=[dict(GOOD, bbox=[1, 1, 5, 5])]),                   # corners 与 bbox 同时给
               reply(detections=[dict(GOOD, angle_deg=5)]),
               reply(detections=[dict(GOOD, label='x' * 65)]),
               reply(detections=[GOOD] * 21), 'not a dict']
        for value in bad:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ProtocolError):
                parse_reply(value, FRAME, SIZE)


class ServiceAndClientTests(unittest.TestCase):
    def setUp(self):
        self.server, self.url = serve(make_handler(DetectService(ColorBlockDetector(), 'color-test')))

    def tearDown(self):
        self.server.shutdown();self.server.server_close()

    def test_round_trip_forwards_original_jpeg(self):
        detector = RemoteDetector(self.url)
        self.assertEqual(detector.health()['model'], 'color-test')
        found = detector.detect_jpeg(yellow_square_jpeg(), FRAME, SIZE)
        self.assertEqual(len(found), 1)
        u, v = np.mean(found[0].corners, axis=0)
        self.assertAlmostEqual(u, 320, delta=3);self.assertAlmostEqual(v, 220, delta=3)
        self.assertIsNotNone(detector.last_ms)

    def test_service_rejects_bad_requests(self):
        def post(data, **headers):
            request = Request(self.url + '/detect', data=data, method='POST', headers=dict(
                {'Content-Type': 'image/jpeg', 'X-Frame-Id': FRAME, 'X-Image-Width': '640', 'X-Image-Height': '480'}, **headers))
            try:
                urlopen(request)
            except Exception as exc:  # noqa: BLE001
                return exc.code
        self.assertEqual(post(b'not a jpeg'), 400)
        self.assertEqual(post(yellow_square_jpeg(), **{'X-Frame-Id': 'bad'}), 400)
        self.assertEqual(post(yellow_square_jpeg(), **{'X-Image-Width': '320'}), 400)     # 尺寸与图不符
        self.assertEqual(post(yellow_square_jpeg(), **{'Content-Type': 'application/json'}), 400)

    def test_client_fails_closed_on_errors_and_extra_fields(self):
        with self.assertRaisesRegex(RemoteDetectError, '连不上'):
            RemoteDetector('http://127.0.0.1:9', timeout_s=1).detect_jpeg(b'x', FRAME, SIZE)
        server, url = fake_service(reply(skip_collision_checks=True))
        try:
            with self.assertRaisesRegex(RemoteDetectError, '协议外字段'):
                RemoteDetector(url).detect_jpeg(yellow_square_jpeg(), FRAME, SIZE)
        finally:
            server.shutdown();server.server_close()


class SimulatedBoardFlowTests(unittest.TestCase):
    """Mac 端 run_block_grasp.py + 远端识别服务 + 本地模拟开发板，整条流程。"""

    def run_flow(self, detector_url):
        import simulate
        from block_vision import run_block_grasp
        from server import demo_handler
        simulate.SimFake.max_delay_ms = 0
        simulate.SimFake.calls.clear()
        with tempfile.TemporaryDirectory() as folder:
            demo = simulate.application(Path(folder))
            board, base = serve(demo_handler(demo))
            try:
                def web(path, body):
                    request = Request(base + path, data=json.dumps(dict(body, csrf=demo.control.csrf)).encode(),
                                      headers={'Content-Type': 'application/json', 'Origin': base})
                    return json.load(urlopen(request))
                offer = web('/modes/preview', dict(kind='block_grasp', task_mode='pickup'))
                session = web('/modes/arm', dict(token=offer['token'], confirmed=True))['session']['id']
                web('/modes/grasp/start', dict(session_id=session))   # 用户点“开始识别夹取”
                logs = []
                run_block_grasp.run(BoardClient(base), RemoteDetector(detector_url, log=logs.append), log=logs.append, once=True)
                events = [json.loads(x) for x in (Path(folder) / 'data/logs/events.jsonl').read_text().splitlines()] \
                    if (Path(folder) / 'data/logs/events.jsonl').exists() else []
                self.status = json.load(urlopen(base + '/modes/status'))
                return logs, list(simulate.SimFake.calls), events
            finally:
                board.shutdown();board.server_close()

    def test_remote_detection_drives_the_normal_board_pipeline(self):
        service, url = serve(make_handler(DetectService(ColorBlockDetector(), 'color-test')))
        try:
            logs, calls, events = self.run_flow(url)
        finally:
            service.shutdown();service.server_close()
        self.assertTrue(any('远端识别 1 个候选' in line for line in logs), logs)
        self.assertTrue(any('任务结束：completed' in line for line in logs), logs)
        self.assertTrue(calls)
        done = [e for e in events if e.get('status') == 'completed']
        self.assertEqual(done[-1]['source'], 'vision')
        self.assertNotIn('skip_collision_checks', done[-1]['target'])

    def test_bad_remote_reply_submits_nothing_and_stops(self):
        server, url = fake_service(reply(detections=[dict(GOOD, skip_collision_checks=True)]))
        try:
            logs, calls, events = self.run_flow(url)
        finally:
            server.shutdown();server.server_close()
        self.assertTrue(any('本次未提交：识别服务出错' in line for line in logs), logs)
        self.assertEqual(calls, []);self.assertEqual(events, [])
        session = self.status['session']        # 会话还在，网页显示原因，等下一次点击
        self.assertIsNone(session['grasp_request'])
        self.assertEqual(session['last_result']['status'], 'not_submitted')
        self.assertIn('识别服务出错', session['last_result']['message'])


if __name__ == '__main__':
    unittest.main()
