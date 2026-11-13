"""D adapter and real B runner tests; HTTP binds only 127.0.0.1, all servos fake."""
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

PC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PC))
from armlink import GestureEvent, BoardClient
from frames import load_class
from gesture.recognizer import CombinedRecognizer, HandRecognizer, SmileRecognizer
from gesture.assets import ASSETS, WEIGHTS, verify_asset
from gesture.download_assets import install
from run_gesture_dance import Debouncer, run


class Evidence:
    def __init__(self, value):
        self.value, self.frames, self.closed = value, [], 0

    def detect(self, image):
        self.frames.append(image)
        return self.value

    def close(self):
        self.closed += 1


def hand(label='palm', confidence=.9, count=1):
    return dict(gesture=label, confidence=confidence, hand_count=count)


def face(score=.4, count=1):
    return dict(status='FACE_DETECTED', face_count=count, smile_score=score)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((240, 320, 3), np.uint8)
        self.hand, self.face = Evidence(hand()), Evidence(face())
        self.rec = CombinedRecognizer(hand_detector=self.hand, smile_detector=self.face)
        self.addCleanup(self.rec.close)

    def test_all_four_hand_labels_and_hand_priority(self):
        for label in ('fist', 'like', 'ok', 'palm'):
            self.hand.value = hand(label)
            event = self.rec.recognize(self.image)
            self.assertEqual(event, GestureEvent(label, .9))
        self.assertIs(self.hand.frames[-1], self.image)
        self.assertIs(self.face.frames[-1], self.image)

    def test_threshold_boundaries_pass_raw_scores_to_runner(self):
        self.hand.value = hand(confidence=.799)
        self.face.value = face(.3)
        event = self.rec.recognize(self.image)
        self.assertEqual(event, GestureEvent('smile', .3))
        self.assertEqual(Debouncer(1, .3).feed(event), event)
        self.face.value = face(.299)
        self.assertIsNone(self.rec.recognize(self.image))
        self.hand.value = hand(confidence=.8)
        self.assertEqual(self.rec.recognize(self.image), GestureEvent('palm', .8))

    def test_non_target_multiple_missing_and_invalid_scores(self):
        self.face.value = face(0)
        for value in (hand(None), hand('wave'), hand(count=2), hand(count=0),
                      hand(confidence=float('nan')), hand(confidence=1.1), hand(confidence=True)):
            self.hand.value = value
            self.assertIsNone(self.rec.recognize(self.image))
        self.hand.value = hand(None)
        for value in (face(.9, 2), face(.9, 0), face(float('inf')), face(-1)):
            self.face.value = value
            self.assertIsNone(self.rec.recognize(self.image))

    def test_none_and_bad_frames_do_not_invoke_models(self):
        for image in (None, [], np.zeros((0, 0, 3), np.uint8),
                      np.zeros((10, 10), np.uint8), np.zeros((10, 10, 3), float)):
            self.assertIsNone(self.rec.recognize(image))
        self.assertEqual(self.hand.frames, [])
        self.assertEqual(self.face.frames, [])

    def test_close_idempotent_and_partial_initialization_cleanup(self):
        self.rec.close();self.rec.close()
        self.assertEqual((self.hand.closed, self.face.closed), (1, 1))
        with self.assertRaises(RuntimeError):
            self.rec.recognize(self.image)
        detector = Evidence(hand())
        with patch('gesture.smile_detector.SmileDetector', side_effect=ValueError('load failed')):
            with self.assertRaises(ValueError):
                CombinedRecognizer(hand_detector=detector)
        self.assertEqual(detector.closed, 1)

    def test_dynamic_loading_single_modes_and_no_required_arguments(self):
        for cls in (CombinedRecognizer, HandRecognizer, SmileRecognizer):
            required = [p for p in inspect.signature(cls).parameters.values()
                        if p.default is p.empty and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)]
            self.assertEqual(required, [])
        with patch('gesture.gesture_detector.GestureDetector', return_value=self.hand), \
             patch('gesture.smile_detector.SmileDetector', return_value=self.face):
            for name, label in (('HandRecognizer', 'palm'), ('SmileRecognizer', 'smile'), ('CombinedRecognizer', 'palm')):
                rec = load_class('gesture.recognizer:' + name)
                self.assertEqual(rec.recognize(self.image).label, label)
                rec.close()

    def test_confirmation_release_and_recognition_only_labels(self):
        debounce = Debouncer(5, .3)
        event = self.rec.recognize(self.image)
        self.assertEqual([debounce.feed(event) for _ in range(7)].count(event), 1)
        debounce.feed(None)
        self.assertEqual([debounce.feed(event) for _ in range(5)].count(event), 1)


class AssetTests(unittest.TestCase):
    def test_changed_weight_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            name = next(iter(ASSETS))
            path = Path(folder) / name
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verify_asset(name, folder)
            with self.assertRaises(ValueError):
                install(folder)
            self.assertEqual(path.read_bytes(), b'changed')


@unittest.skipUnless(all((WEIGHTS / n).is_file() for n in ASSETS), 'local model weights unavailable')
class ModelSmokeTests(unittest.TestCase):
    def test_real_models_load_and_blank_frame_returns_none(self):
        rec = CombinedRecognizer()
        try:
            self.assertIsNone(rec.recognize(None))
            self.assertIsNone(rec.recognize(np.zeros((480, 640, 3), np.uint8)))
        finally:
            rec.close()


class LocalSimulationTests(unittest.TestCase):
    def test_smile_course_and_palm_music_via_b_runner_http(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        board = PC.parent / 'board'
        sys.path[:0] = [str(board), str(board / 'tests')]
        import simulate
        from server import demo_handler
        image = np.zeros((100, 100, 3), np.uint8)
        class Source:
            def read(self):
                return image
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(simulate.SimFake, 'max_delay_ms', 0):
                demo = simulate.application(Path(folder))
                handler = demo_handler(demo)
                handler.log_message = lambda *a: None
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}'
                client = BoardClient(base)
                try:
                    self.assertEqual(client.health()['mode'], 'simulation')
                    def web(path, body):
                        request = Request(base + path, data=json.dumps(dict(body, csrf=demo.control.csrf)).encode(),
                                          headers={'Content-Type': 'application/json', 'Origin': base})
                        with urlopen(request) as response:
                            return json.load(response)
                    for label, routine in (('smile', 'course'), ('palm', 'music')):
                        offer = web('/modes/preview', dict(kind='gesture_dance', base_state_id='0' * 32,
                                    routines=[routine], max_actions=1, cooldown_s=0))
                        web('/modes/arm', dict(token=offer['token'], confirmed=True))
                        rec = CombinedRecognizer(hand_detector=Evidence(hand('palm') if label == 'palm' else hand(None)),
                                                 smile_detector=Evidence(face(.4)))
                        logs = []
                        try:
                            run(client, rec, Source(), {'smile': 'course', 'palm': 'music'},
                                stable=5, threshold=.3, log=logs.append, max_seconds=30)
                        finally:
                            rec.close()
                        self.assertTrue(any('跳舞结束：completed' in line for line in logs), logs)
                        self.assertTrue(any(f'跳 {routine}' in line for line in logs), logs)
                        self.assertFalse(client.mode()['armed'])
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
