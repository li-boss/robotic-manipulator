"""语音模式电脑端：舵机文字解析，以及对本地全模拟开发板的八类命令联调。假舵机 + 合成相机，不连接硬件。"""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

PC = Path(__file__).resolve().parents[2]
BOARD = PC.parent / 'board'
sys.path[:0] = [str(PC), str(PC.parent), str(BOARD), str(BOARD / 'tests')]

from armlink import BoardClient, GestureEvent  # noqa: E402
from pc.voice_control.actions import VoiceMode, requested_color  # noqa: E402
from pc.voice_control.actions import chinese_number, parse_servo_command  # noqa: E402


class ServoTextTests(unittest.TestCase):
    def test_numbers(self):
        cases = {'90': 90, '九十': 90, '十五': 15, '一百': 100, '一百零五': 105, '一百五': 150, '一百三十五': 135,
                 '两百': 200, '二百七十': 270, '一三五': 135, '零': 0, '十': 10}
        for text, value in cases.items():
            self.assertEqual(chinese_number(text), value, text)
        for bad in ('', '百', '一十十', 'abc'):
            with self.assertRaises(ValueError):
                chinese_number(bad)

    def test_dataset_style_commands(self):
        cases = {'一号舵机转到90度': (1, 90), '2号舵机转到120度': (2, 120), '把3号舵机调到80度': (3, 80),
                 '四号舵机转到一百度': (4, 100), '5号舵机设置为150度': (5, 150), '6号舵机调成40度': (6, 40),
                 '把六号舵机转成70度': (6, 70), '第三关节设定在115度': (3, 115), '5号舵机旋到200度': (5, 200),
                 '1号舵机调到135度': (1, 135), '把 二号 舵机 转到 九十 度': (2, 90)}
        for text, expected in cases.items():
            self.assertEqual(parse_servo_command(text), expected, text)

    def test_ambiguous_or_fractional_commands_are_refused(self):
        for text in ('舵机转到90度', '一号舵机转一下', '一号和二号舵机转到90度', '一号舵机转到90度再到80度',
                     '七号舵机转到90度', '一号舵机转到九十点五度', '1号舵机转到90.5度', None):
            with self.assertRaises(ValueError, msg=text):
                parse_servo_command(text)

    def test_color_words(self):
        self.assertEqual(requested_color('抓红色方块'), 'red')
        self.assertIsNone(requested_color('抓方块'))
        with self.assertRaises(ValueError):
            requested_color('抓红色和蓝色方块')


class FakeRecognizer:
    def __init__(self, labels):
        self.labels = list(labels)

    def recognize(self, image):
        label = self.labels.pop(0) if self.labels else None
        return GestureEvent(label, .9) if label else None


class FakeSource:
    def read(self):
        return None


class SimulatedBoardTests(unittest.TestCase):
    def test_every_command_against_simulated_board(self):
        from block_vision.color_detector import ColorBlockDetector
        import json
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        import simulate
        from server import demo_handler
        simulate.SimFake.max_delay_ms = 0
        with tempfile.TemporaryDirectory() as folder:
            demo = simulate.application(Path(folder))
            handler = demo_handler(demo)
            handler.log_message = lambda *a: None
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                client = BoardClient(base)
                logs = []
                voice = VoiceMode(client, logs.append, detector=ColorBlockDetector(), source=FakeSource(),
                                  mapping=dict(smile='COURSE_DANCE_ROUTINE', palm='MUSIC_DANCE_ROUTINE'),
                                  recog_seconds=5, stable=2)
                self.assertFalse(voice.handle('POSE_VERTICAL', '回到竖直'))           # 没开会话：不执行
                self.assertTrue(any('没有开启的语音会话' in line for line in logs), logs)

                def web(path, body):
                    body = dict(body, csrf=demo.control.csrf)
                    request = Request(base + path, data=json.dumps(body).encode(),
                                      headers={'Content-Type': 'application/json', 'Origin': base})
                    return json.load(urlopen(request))
                offer = web('/modes/preview', dict(kind='voice_control', vertical_state_id='0' * 32,
                                                   observe_state_id='1' * 32, servo_motion_ms=800,
                                                   destination='模拟放置点', max_actions=20, cooldown_s=0))
                web('/modes/arm', dict(token=offer['token'], confirmed=True))

                # 模拟器的方块只在起始姿态的画面里，先抓方块，再做其他动作。
                steps = [('START_BLOCK_GRASP', '抓蓝色方块', False),                  # 画面里只有黄色方块
                         ('START_BLOCK_GRASP', '抓方块放到左边', False),              # 不支持语音指定放置点
                         ('START_BLOCK_GRASP', '帮我抓一下方块', True),
                         ('SET_SERVO_ABS_ANGLE', '把三号舵机转到一百度', True),
                         ('SET_SERVO_ABS_ANGLE', '一号舵机转到二百度', False),        # 超出范围：开发板拒绝
                         ('SET_SERVO_ABS_ANGLE', '舵机转一下', False),                # 听不清：不提交
                         ('POSE_OBSERVE', '回到观察姿态', True),
                         ('POSE_VERTICAL', '回到竖直姿态', True),
                         ('COURSE_DANCE_ROUTINE', '跳课程舞蹈', True)]
                for action, text, ok in steps:
                    self.assertEqual(voice.handle(action, text), ok, (text, logs[-3:]))
                # 表情识别：只认 smile；手势识别：只认 palm 等非表情标签
                voice.recognizer = FakeRecognizer(['palm', 'palm', 'smile', 'smile'])
                self.assertTrue(voice.handle('START_EXPR_RECOG', '开始表情识别'))
                job = demo.control.job
                self.assertEqual((job['parameters']['routine'], job['parameters']['trigger']['label']),
                                 ('COURSE_DANCE_ROUTINE', 'smile'))
                voice.recognizer = FakeRecognizer(['smile', 'smile'])
                voice.recog_seconds = .3
                self.assertFalse(voice.handle('START_GESTURE_RECOG', '开始手势识别'))   # 只有表情：手势识别不触发
                used = client.mode()['used']
                self.assertEqual(used, 6)
                self.assertTrue(any('没有找到方块' in line for line in logs), logs)
            finally:
                server.shutdown()
                server.server_close()


class ClassifierSmokeTests(unittest.TestCase):
    """用语音同学训练出的分类模型检查几句典型命令（模型文件不入库，没有就跳过）。"""

    def test_typical_commands(self):
        try:
            from pc.voice_control.text_classification.classifier import IntentClassifier
            classifier = IntentClassifier()
        except (ImportError, FileNotFoundError, ValueError) as exc:
            self.skipTest(f'分类模型不可用：{exc}')
        expected = {'跳一段音乐舞蹈': 'MUSIC_DANCE_ROUTINE', '回到竖直姿态': 'POSE_VERTICAL',
                    '把三号舵机转到九十度': 'SET_SERVO_ABS_ANGLE', '开始手势识别': 'START_GESTURE_RECOG'}
        for text, action in expected.items():
            self.assertEqual(classifier.predict(text).action, action, text)


if __name__ == '__main__':
    unittest.main()
