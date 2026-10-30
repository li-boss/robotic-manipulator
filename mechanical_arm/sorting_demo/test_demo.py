"""全部使用合成图片和假控制器；不连接摄像头、网络或机械臂。"""
import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from core import EventStore, detect, simulate, statistics, task_plan
from run import execute_real

ROOT = Path(__file__).resolve().parent


def config():
    value = json.loads((ROOT / "config.json").read_text())
    value.update(roi=[0, 0, 200, 200], vision_reviewed=True)
    return value


def arm_config():
    # 软件测试夹具；绝不部署到设备配置。
    return dict(hardware_ready=True, motion_ms=1500, settle_ms=300,
                joint_limits={str(i): list(bounds) for i, bounds in __import__('core').arm.HARD_LIMITS.items()},
                gripper=dict(open=60, closed=120),
                poses={key: [90] * 5 for key in ("ready", "pickup_above", "pickup")},
                targets=dict(yellow=dict(above=[90] * 5, drop=[90] * 5)))


def image(two=False, unknown=False):
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.rectangle(frame, (30, 40), (70, 80), (255, 0, 255) if unknown else (0, 255, 255), -1)
    if two:
        cv2.rectangle(frame, (110, 100), (150, 140), (255, 0, 0), -1)
    return frame


class Tests(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)

    def test_detection_center_and_guards(self):
        result = detect(image(), config())
        self.assertEqual((result["result"], result["color"], result["pixel_x"], result["pixel_y"]),
                         ("detected", "yellow", 50, 60))
        for frame, expected in [(image(two=True), "multiple_targets"), (image(unknown=True), "unknown_color"),
                                (np.zeros((200, 200, 3), np.uint8), "no_target")]:
            self.assertEqual(detect(frame, config())["result"], expected)

    def test_roi_and_review_required(self):
        self.assertEqual(detect(image(), dict(config(), roi=None))["result"], "unconfigured")
        self.assertEqual(detect(image(), dict(config(), roi=[0, 0, 300, 300]))["result"], "invalid_roi")
        self.assertEqual(detect(image(), dict(config(), vision_reviewed=False))["result"], "unreviewed")
        self.assertEqual(detect(image(), dict(config(), roi=[90, 90, 200, 200]))["result"], "no_target")

    def test_ambiguous_color_rejected(self):
        cfg = config()
        cfg["colors"]["other"] = cfg["colors"]["yellow"]
        self.assertEqual(detect(image(), cfg)["result"], "unknown_color")

    def test_invalid_hsv_roi_and_values(self):
        for fields in [dict(roi=[True, 0, 10, 10]), dict(min_area=0), dict(min_color_fraction=float('nan')),
                       dict(colors={"x": [[0, 0, 0], [180, 255, 255]]}), dict(colors={"x": [[0], [1]]})]:
            with self.assertRaises(ValueError):
                detect(image(), dict(config(), **fields))

    def test_expired_and_missing_motion_config_rejected(self):
        detection = detect(image(), config())
        with self.assertRaises(ValueError):
            task_plan(dict(detection, captured_at=0), config(), arm_config())
        with self.assertRaises(ValueError):
            task_plan(dict(detection, captured_at=int(time.time()*1000)+10000), config(), arm_config())
        missing = arm_config()
        missing["joint_limits"]["2"] = None
        with self.assertRaises(ValueError):
            task_plan(detection, config(), missing)

    def test_simulation_idempotence_and_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.jsonl")
            detection = detect(image(), config())
            with patch('core.arm.Controller', side_effect=AssertionError('模拟禁止初始化硬件')):
                event = simulate(detection, config(), arm_config(), store)
                self.assertEqual(event, simulate(detection, config(), arm_config(), store))
            events, errors = store.latest()
            self.assertEqual((len(events), errors, statistics(events)["success"]), (1, 0, 0))
            self.assertEqual(event["result"], "simulated")

    def test_rejected_plan_logged_and_bad_log_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.jsonl")
            event = simulate(detect(image(), config()), config(), {}, store)
            self.assertEqual(event['status'], 'rejected')
            with store.path.open('a') as stream:
                stream.write('{bad\n')
            self.assertEqual(store.latest()[1], 1)

    def test_real_missing_path_never_initializes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.jsonl")
            with self.assertRaises(ValueError):
                execute_real(detect(image(), config()), config(), arm_config(), store, None,
                             factory=lambda *a, **k: self.fail('不应初始化硬件'))

    def test_real_abort_and_failure_stop_no_retry(self):
        for fail_at in (None, 2):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as folder:
                cfg = dict(config(), verified_paths=['yellow'])
                store = EventStore(Path(folder) / 'events.jsonl')
                calls = []
                class Fake:
                    def execute(self, kind, name):
                        calls.append((kind, name))
                        if len(calls) == fail_at:
                            raise RuntimeError('通信失败')
                answers = iter(['y', 'n'] if fail_at is None else ['y'] * 12)
                with patch('core.arm.ROOT', Path(folder)):
                    with self.assertRaises((InterruptedError, RuntimeError)):
                        execute_real(detect(image(), cfg), cfg, arm_config(), store,
                                     lambda: detect(image(), cfg), lambda _: next(answers),
                                     lambda *a, **k: Fake())
                self.assertEqual(len(calls), 0 if fail_at is None else fail_at)
                self.assertEqual(store.latest()[0][0]['retry_count'], 0)
                self.assertEqual(store.latest()[0][0]['status'], 'aborted')

    def test_log_failure_prevents_hardware_init(self):
        cfg = dict(config(), verified_paths=['yellow'])
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder))  # 目录不能作为日志写入
            with patch('core.arm.ROOT', Path(folder)):
                with self.assertRaises(OSError):
                    execute_real(detect(image(), cfg), cfg, arm_config(), store, lambda: detect(image(), cfg),
                                 lambda _: 'y', lambda *a, **k: self.fail('日志失败不初始化'))

    def test_service_cache_disconnect_and_settings_guard(self):
        # 只使用内存JPEG，绝不打开VideoCapture或真实摄像头。
        import importlib.util
        spec = importlib.util.spec_from_file_location('demo_web_test', ROOT / 'server.py')
        web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web)
        class Camera:
            condition = threading.Condition()
            sequence = 1
            updated = time.monotonic()
            jpeg = cv2.imencode('.jpg', image())[1].tobytes()
            connected = True
            def status(self):
                return dict(connected=self.connected, message='离线')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(config()))
            camera = Camera()
            service = web.Demo(camera, path, log_path=Path(folder) / 'events.jsonl')
            first = service.detection()
            self.assertEqual(first['request_id'], service.detection()['request_id'])
            with self.assertRaises(ValueError):
                service.settings({'verified_paths': ['yellow']})
            service.settings({'vision_reviewed': False})
            self.assertEqual(service.detection()['result'], 'unreviewed')
            camera.connected = False
            self.assertEqual(service.detection()['result'], 'camera_unavailable')
            self.assertIsNone(service.cached)


if __name__ == '__main__':
    unittest.main()
