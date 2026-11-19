"""局域网表情手势识别（jjt-gesture/1）与地址配置文件：协议校验、识别服务、Mac 端转发，
以及对本地模拟开发板的整条“识别 → 跳舞”流程。全部在本机进行，不连接机械臂。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

import numpy as np

PC = Path(__file__).resolve().parents[1]
BOARD = PC.parent / 'board'
sys.path[:0] = [str(PC), str(BOARD), str(BOARD / 'tests')]

from armlink import BoardClient, GestureEvent  # noqa: E402
from detect_protocol import ProtocolError  # noqa: E402
from gesture.base import GestureRecognizer  # noqa: E402
from gesture_protocol import PROTOCOL, parse_reply  # noqa: E402
from gesture_server import GestureService, make_handler  # noqa: E402
import lan_config  # noqa: E402
from remote_recognizer import RemoteProtocolError, RemoteRecognizeError, RemoteRecognizer  # noqa: E402

FRAME = 'ab' * 16
IMAGE = np.full((480, 640, 3), 90, np.uint8)


def reply(**extra):
    return dict(dict(protocol=PROTOCOL, frame_id=FRAME, model='m', ms=1.0, event=dict(label='smile', confidence=.9)), **extra)


def serve(handler):
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


class Always(GestureRecognizer):
    def __init__(self, label='smile', confidence=.9):
        self.event = None if label is None else GestureEvent(label, confidence)
        self.seen = 0

    def recognize(self, image):
        self.seen += 1
        return self.event


class FrameSource:
    def read(self):
        return IMAGE

    def close(self):
        pass


def fake_service(payload):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, value):
            body = json.dumps(value).encode()
            self.send_response(200);self.send_header('Content-Length', str(len(body)));self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(dict(protocol=PROTOCOL, ready=True, model='fake'))

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self._send(dict(payload, frame_id=self.headers['X-Frame-Id']))
    return serve(Handler)


class ProtocolTests(unittest.TestCase):
    def test_valid_replies(self):
        self.assertEqual(parse_reply(reply(), FRAME), GestureEvent('smile', .9))
        self.assertIsNone(parse_reply(reply(event=None), FRAME))

    def test_anything_outside_the_protocol_is_rejected(self):
        bad = [reply(routine='MUSIC_DANCE_ROUTINE'),                       # 不能指定舞蹈
               reply(event=dict(label='smile', confidence=.9, joints=[90] * 6)),
               reply(protocol='jjt-gesture/0'), reply(frame_id='cd' * 16),
               {k: v for k, v in reply().items() if k != 'event'},           # 缺 event
               reply(event=dict(label='', confidence=.9)), reply(event=dict(label='x' * 65, confidence=.9)),
               reply(event=dict(label='smile', confidence=1.5)), reply(event=dict(label='smile', confidence=True)),
               reply(event=dict(label='smile', confidence=float('nan'))), reply(event='smile'), 'not a dict']
        for value in bad:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ProtocolError):
                parse_reply(value, FRAME)


class ConfigTests(unittest.TestCase):
    def write(self, folder, value):
        path = Path(folder) / 'lan.json'
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        return path

    def test_load_and_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            config = lan_config.load(self.write(folder, dict(_说明='x', board='http://192.168.137.100:8080/',
                                                             block_detect_url='http://10.0.0.5:8100', gesture_url='')))
            self.assertEqual(config, dict(board='http://192.168.137.100:8080', block_detect_url='http://10.0.0.5:8100',
                                          gesture_url=''))
            self.assertEqual(lan_config.load(Path(folder) / 'missing.json')['gesture_url'], '')   # 没文件 = 本机模型
            for bad in (dict(detect='http://a:1'), dict(gesture_url='10.0.0.5:8101'), dict(gesture_url='https://a:1'),
                        dict(gesture_url='http://a:1/recognize'), dict(board=''), '{oops'):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    lan_config.load(self.write(folder, bad))

    def test_shipped_config_is_valid(self):
        config = lan_config.load()
        self.assertTrue(config['board'].startswith('http://'))


class ServiceAndClientTests(unittest.TestCase):
    def test_round_trip_and_none(self):
        for label, expected in (('smile', GestureEvent('smile', .9)), (None, None)):
            server, url = serve(make_handler(GestureService(Always(label), 'test-model')))
            try:
                remote = RemoteRecognizer(url)
                self.assertEqual(remote.health()['model'], 'test-model')
                self.assertEqual(remote.recognize(IMAGE), expected)
                self.assertIsNone(remote.recognize(None))           # 无画面不发请求
            finally:
                server.shutdown();server.server_close()

    def test_service_rejects_bad_requests(self):
        server, url = serve(make_handler(GestureService(Always(), 'm')))
        try:
            request = Request(url + '/recognize', data=b'not a jpeg', method='POST', headers={
                'Content-Type': 'image/jpeg', 'X-Frame-Id': FRAME, 'X-Image-Width': '640', 'X-Image-Height': '480'})
            with self.assertRaises(Exception) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown();server.server_close()

    def test_client_errors(self):
        with self.assertRaises(RemoteRecognizeError):
            RemoteRecognizer('http://127.0.0.1:9', timeout_s=1).recognize(IMAGE)
        server, url = fake_service(reply(routine='MUSIC_DANCE_ROUTINE'))
        try:
            with self.assertRaisesRegex(RemoteProtocolError, '协议外字段'):
                RemoteRecognizer(url).recognize(IMAGE)
        finally:
            server.shutdown();server.server_close()


class SimulatedBoardFlowTests(unittest.TestCase):
    """Mac 端 run_gesture_dance.py + 远端识别服务 + 本地模拟开发板。"""

    def run_flow(self, url):
        import simulate
        import run_gesture_dance
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
                state = json.load(urlopen(base + '/modes/status'))['saved_states'][0]['id']
                offer = web('/modes/preview', dict(kind='gesture_dance', base_state_id=state, max_actions=1, cooldown_s=0,
                                                   routines=['MUSIC_DANCE_ROUTINE']))
                web('/modes/arm', dict(token=offer['token'], confirmed=True))
                logs = []
                run_gesture_dance.run(BoardClient(base), RemoteRecognizer(url, log=logs.append), FrameSource(),
                                      {'smile': 'MUSIC_DANCE_ROUTINE'}, stable=3, threshold=.2, log=logs.append,
                                      max_seconds=60)
                return logs, list(simulate.SimFake.calls)
            finally:
                board.shutdown();board.server_close()

    def test_remote_gesture_triggers_one_authorized_dance(self):
        recognizer = Always('smile', .9)
        service, url = serve(make_handler(GestureService(recognizer, 'test-model')))
        try:
            logs, calls = self.run_flow(url)
        finally:
            service.shutdown();service.server_close()
        self.assertTrue(any('→ 跳 MUSIC_DANCE_ROUTINE' in line for line in logs), logs)
        self.assertTrue(any('跳舞结束：completed' in line for line in logs), logs)
        self.assertTrue(any('会话已结束' in line for line in logs), logs)
        self.assertTrue(calls)
        self.assertGreaterEqual(recognizer.seen, 3)

    def test_bad_remote_reply_stops_without_motion(self):
        server, url = fake_service(reply(routine='MUSIC_DANCE_ROUTINE'))
        try:
            logs, calls = self.run_flow(url)
        finally:
            server.shutdown();server.server_close()
        self.assertTrue(any('不合协议，程序停止' in line for line in logs), logs)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
