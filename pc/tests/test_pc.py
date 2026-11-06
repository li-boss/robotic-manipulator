"""电脑端：数据结构、防抖、示例检测器，以及对本地全模拟开发板服务的 HTTP 联调。不连接硬件。"""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import cv2
import numpy as np

PC = Path(__file__).resolve().parents[1]
BOARD = PC.parent / 'board'
sys.path[:0] = [str(PC), str(BOARD), str(BOARD / 'tests')]

from armlink import BlockDetection, BoardClient, BoardError, GestureEvent  # noqa: E402
from block_vision.color_detector import ColorBlockDetector  # noqa: E402
from run_gesture_dance import Debouncer, parse_map  # noqa: E402


class TypesTests(unittest.TestCase):
    def test_block_detection_serialization(self):
        d = BlockDetection(corners=np.array([[1, 2], [3, 2], [3, 4], [1, 4]]), score=np.float32(.7), label='yellow')
        self.assertEqual(d.to_dict(), dict(score=.699999988079071, corners=[[1, 2], [3, 2], [3, 4], [1, 4]], label='yellow'))
        self.assertEqual(BlockDetection(bbox=[1, 2, 30, 40], angle_deg=15).to_dict()['angle_deg'], 15.0)
        with self.assertRaises(ValueError):
            BlockDetection().to_dict()
        with self.assertRaises(ValueError):
            BlockDetection(corners=[[0, 0]] * 3).to_dict()

    def test_debouncer_and_map(self):
        d = Debouncer(stable=3, threshold=.6)
        seq = [GestureEvent('smile', .9)] * 5 + [None, GestureEvent('smile', .9), GestureEvent('smile', .5)] + [GestureEvent('wave', .9)] * 3
        fired = [e.label for e in map(d.feed, seq) if e]
        self.assertEqual(fired, ['smile', 'wave'])
        self.assertEqual(parse_map('smile=sway, wave = circle'), dict(smile='sway', wave='circle'))
        with self.assertRaises(SystemExit):
            parse_map('smile')

    def test_color_detector_finds_rotated_square(self):
        image = np.full((480, 640, 3), 40, np.uint8)
        box = cv2.boxPoints(((320, 240), (60, 60), 30)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (40, 210, 245))
        found = ColorBlockDetector().detect(image)
        self.assertEqual(len(found), 1)
        np.testing.assert_allclose(np.mean(found[0].corners, axis=0), [320, 240], atol=2)
        self.assertEqual(ColorBlockDetector().detect(np.zeros((480, 640, 3), np.uint8)), [])


class SimulatedBoardTests(unittest.TestCase):
    def test_client_against_simulated_board(self):
        import json
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        import simulate
        from server import demo_handler
        simulate.SimFake.max_delay_ms = 0
        with tempfile.TemporaryDirectory() as folder:
            demo = simulate.application(Path(folder))
            handler = demo_handler(demo);handler.log_message = lambda *a: None
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                client = BoardClient(base)
                self.assertEqual(client.health()['api_version'], 1)
                self.assertFalse(client.mode()['armed'])
                with self.assertRaises(BoardError) as caught:
                    client.freeze_frame('a' * 32)
                self.assertEqual(caught.exception.status, 400)
                self.assertTrue(client.snapshot().startswith(b'\xff\xd8'))
                # 网页侧开启（同源 + csrf）
                def web(path, body):
                    body = dict(body, csrf=demo.control.csrf)
                    request = Request(base + path, data=json.dumps(body).encode(),
                                      headers={'Content-Type': 'application/json', 'Origin': base})
                    return json.load(urlopen(request))
                offer = web('/modes/preview', dict(kind='block_grasp', task_mode='pickup'))
                web('/modes/arm', dict(token=offer['token'], confirmed=True))
                import run_block_grasp
                logs = []
                run_block_grasp.run(client, ColorBlockDetector(), log=logs.append, once=True)
                self.assertTrue(any('任务结束：completed' in line for line in logs), logs)
                self.assertFalse(client.mode()['armed'])     # 只夹取模式 1 次后会话结束
            finally:
                server.shutdown();server.server_close()


if __name__ == '__main__':
    unittest.main()
