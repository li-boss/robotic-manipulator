"""8099 console: fake board responses and synthetic images, no real hardware."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vision_service as service
from armlink import BlockDetection, BoardError


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.error = None
        self.frame = 'a' * 32
        self.mode = dict(armed=True, kind='block_grasp', session_id='s1', busy=False, min_score=.5)
        self.payload = BlockDetection(corners=[(10, 10), (30, 10), (30, 30), (10, 30)],
                                      center=(20, 20), score=.9, label='block_blue')
        self.raw = cv2.imencode('.jpg', np.zeros((48, 64, 3), np.uint8))[1].tobytes()
        self.result = dict(targets=[], parameters=dict(conf=.25))
        self.detector = SimpleNamespace(analyze=lambda _: self.result, candidates=lambda _: [self.payload])
        self.addCleanup(patch.stopall)
        patch.dict(service.STATE, freeze=None, busy=False, args=SimpleNamespace(
            out=Path(self.tmp.name), max_age=90, label='auto'), detector=self.detector).start()
        patch.object(service, 'board', side_effect=self.board).start()
        patch.object(service, 'overlay', side_effect=lambda image, _: image).start()

    def board(self, path, body=None, timeout=None):
        self.calls.append((path, body))
        if path == '/api/v1/mode':
            return self.mode.copy()
        if path == '/api/v1/health':
            return dict(mode='real', camera=dict(connected=True))
        if path == '/api/v1/block/frame':
            return dict(frame_id=self.frame, id=self.frame, image_size=[64, 48],
                        joints=[dict(id=i, angle=90) for i in range(1, 7)], captured_at=123,
                        jpeg_base64=base64.b64encode(self.raw).decode())
        if path == '/api/v1/block/target':
            if self.error:
                raise self.error
            return dict(job=dict(id='job1', status='running'))
        raise AssertionError('Unexpected board request: ' + path)

    @staticmethod
    def binding(preview):
        return {k: preview[k] for k in ('freeze_id', 'frame_id', 'session_id')}

    def submissions(self):
        return [body for path, body in self.calls if path == '/api/v1/block/target']

    def test_exact_frame_and_pixels_submitted_once_with_evidence(self):
        preview = service.do_freeze('auto')
        service.do_grasp(self.binding(preview))
        self.assertEqual(self.submissions(), [dict(session_id='s1', frame_id=self.frame,
                                                  detection=self.payload.to_dict())])
        evidence = Path(preview['evidence_dir'])
        self.assertEqual((evidence / 'raw.jpg').read_bytes(), self.raw)
        self.assertEqual(json.loads((evidence / 'capture.json').read_text())['frame_id'], self.frame)
        result = json.loads((evidence / 'submission_result.json').read_text())
        self.assertEqual(result['status'], 'accepted')
        with self.assertRaises(BoardError):
            service.do_grasp(self.binding(preview))
        self.assertEqual(len(self.submissions()), 1)

    def test_old_tab_cannot_submit_replacement_frame(self):
        old = service.do_freeze('auto')
        self.frame = 'b' * 32
        new = service.do_freeze('auto')
        with self.assertRaisesRegex(BoardError, '不一致'):
            service.do_grasp(self.binding(old))
        self.assertEqual(self.submissions(), [])
        service.do_grasp(self.binding(new))
        self.assertEqual(self.submissions()[0]['frame_id'], self.frame)

    def test_failed_new_freeze_invalidates_old_target(self):
        old = service.do_freeze('auto')
        self.detector.candidates = lambda _: []
        with self.assertRaisesRegex(BoardError, '没有可用候选'):
            service.do_freeze('auto')
        self.assertIsNone(service.STATE['freeze'])
        with self.assertRaises(BoardError):
            service.do_grasp(self.binding(old))
        self.assertEqual(self.submissions(), [])

    def test_collision_rejection_keeps_error_and_exact_target(self):
        preview = service.do_freeze('auto')
        self.error = BoardError('接近抓取：约230毫米，CAD连杆、相机或夹爪包络接近桌面', 400)
        with self.assertRaisesRegex(BoardError, '230毫米'):
            service.do_grasp(self.binding(preview))
        evidence = Path(preview['evidence_dir'])
        result = json.loads((evidence / 'submission_result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['error'], str(self.error))
        self.assertEqual(json.loads((evidence / 'request.json').read_text())['detection'], self.payload.to_dict())
        self.assertIsNone(service.STATE['freeze'])

    def test_timeout_is_unknown_and_cannot_resend(self):
        preview = service.do_freeze('auto')
        self.error = TimeoutError('response timed out')
        with self.assertRaisesRegex(BoardError, '状态未知'):
            service.do_grasp(self.binding(preview))
        result = json.loads((Path(preview['evidence_dir']) / 'submission_result.json').read_text())
        self.assertEqual(result['status'], 'unknown')
        with self.assertRaises(BoardError):
            service.do_grasp(self.binding(preview))
        self.assertEqual(len(self.submissions()), 1)

    def test_missing_binding_or_changed_session_never_submits(self):
        preview = service.do_freeze('auto')
        with self.assertRaises(BoardError):
            service.do_grasp({})
        self.mode['session_id'] = 's2'
        with self.assertRaisesRegex(BoardError, '会话'):
            service.do_grasp(self.binding(preview))
        self.assertEqual(self.submissions(), [])

    def test_parallel_operations_are_rejected_without_replacing_preview(self):
        preview = service.do_freeze('auto')
        with service.operation():
            for operation in (lambda: service.do_freeze('auto'), lambda: service.do_grasp(self.binding(preview))):
                with self.assertRaisesRegex(BoardError, '正在处理'):
                    operation()
        self.assertEqual(service.STATE['freeze']['freeze_id'], preview['freeze_id'])
        self.assertFalse(service.STATE['busy'])
        self.assertEqual(self.submissions(), [])

    def test_inference_time_counts_towards_preview_expiry(self):
        with patch.object(service.time, 'time', return_value=100):
            preview = service.do_freeze('auto')
        with patch.object(service.time, 'time', return_value=191):
            with self.assertRaisesRegex(BoardError, '过期'):
                service.do_grasp(self.binding(preview))
        self.assertEqual(self.submissions(), [])

    def test_http_page_and_bound_submission_work_together(self):
        server = service.ThreadingHTTPServer(('127.0.0.1', 0), service.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            def request(path, body=None):
                data = None if body is None else json.dumps(body).encode()
                with urllib.request.urlopen(base + path, data=data, timeout=3) as response:
                    return response.read(), response.headers
            page, headers = request('/')
            self.assertIn(b'/console.js', page)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            script, _ = request('/console.js')
            self.assertIn(b'freeze_id', script)
            preview = json.loads(request('/api/freeze?label=auto', {})[0])
            self.assertNotIn('error', preview)
            invalid = json.loads(request('/api/grasp', {})[0])
            self.assertIn('error', invalid)
            self.assertEqual(self.submissions(), [])
            result = json.loads(request('/api/grasp', self.binding(preview))[0])
            self.assertEqual(result['response']['job']['id'], 'job1')
            self.assertEqual(len(self.submissions()), 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
