"""本地模拟开发板的 HTTP 抓取链路；所有舵机均为假设备。"""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

PC = Path(__file__).resolve().parents[2]
BOARD = PC.parent / 'board'
sys.path[:0] = [str(PC), str(BOARD), str(BOARD / 'tests')]

from armlink import BoardClient, BoardError  # noqa: E402
from block_vision.color_detector import ColorBlockDetector  # noqa: E402
from block_vision import run_block_grasp  # noqa: E402


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
                logs = []
                run_block_grasp.run(client, ColorBlockDetector(), log=logs.append, once=True)
                self.assertTrue(any('任务结束：completed' in line for line in logs), logs)
                self.assertFalse(client.mode()['armed'])     # 只夹取模式 1 次后会话结束
            finally:
                server.shutdown();server.server_close()


if __name__ == '__main__':
    unittest.main()
