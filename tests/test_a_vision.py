import json
import tempfile
import unittest
import time
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from calibration_tools.samples.collect import collect
from camera_service.camera import FrameError, FrameStore
from camera_service.frame_source import CaptureWorker, image_size
from camera_service.service import VisionService, handler_for
from waste_vision.detector import Detector, FailedBackend, ImageTransform, MockBackend, ModelError, UnavailableBackend


JPEG = bytes.fromhex("ffd8 ffc0 0011 08 0002 0003 03 011100 021100 031100 ffd9")


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.mapping = root / "map.json"
        self.mapping.write_text(json.dumps({"blue_block": None, "paper": "可回收物"}), encoding="utf-8")
        self.fixture = root / "mock.json"
        self.fixture.write_text(json.dumps({"boxes": [
            {"label": "blue_block", "confidence": 0.9, "bbox": [10, 20, 30, 40]},
            {"label": "unknown", "confidence": 0.2, "bbox": [40, 50, 60, 70]}]}))
        self.store = FrameStore("cam1", max_age_ms=100)

    def test_image_and_failed_image(self):
        self.assertEqual(image_size(JPEG), (3, 2))
        with self.assertRaises(ValueError):
            image_size(b"not an image")
        worker = CaptureWorker(self.store, "image", str(Path(self.temp.name) / "missing.jpg"))
        worker.start()
        worker.stop()
        self.assertIn("FileNotFoundError", worker.error)
        with self.assertRaises(FrameError):
            self.store.get("latest")

    def test_mjpeg_timeout_path_never_republishes_last_image(self):
        import numpy as np

        class FakeCapture:
            def __init__(self, opened, frames):
                self.opened, self.frames = opened, list(frames)

            def isOpened(self):
                return self.opened

            def read(self):
                return self.frames.pop(0) if self.frames else (False, None)

            def release(self):
                pass

        class FakeCv:
            CAP_FFMPEG = 1900
            CAP_PROP_OPEN_TIMEOUT_MSEC = 53
            CAP_PROP_READ_TIMEOUT_MSEC = 54

            def __init__(self):
                self.calls = []

            def VideoCapture(self, *args):
                self.calls.append(args)
                if len(self.calls) == 1:
                    image = np.zeros((2, 3, 3), dtype=np.uint8)
                    return FakeCapture(True, [(True, image), (True, image), (False, None)])
                return FakeCapture(False, [])

            def imencode(self, *_args):
                return True, np.array([1, 2, 3], dtype=np.uint8)

        fake = FakeCv()
        store = FrameStore("mjpeg", max_age_ms=100)
        worker = CaptureWorker(store, "camera", "http://board/stream.mjpg", fps_limit=1)
        with patch("camera_service.frame_source._cv2", return_value=fake):
            worker.start()
            deadline = time.monotonic() + 1
            while (len(store._frames) < 2 or store.available) and time.monotonic() < deadline:
                time.sleep(.01)
            worker.stop()
        self.assertEqual(len(store._frames), 2)
        self.assertFalse(store.available)
        self.assertEqual(worker.error, "CAMERA_UNAVAILABLE")
        self.assertEqual(fake.calls[0], ("http://board/stream.mjpg", fake.CAP_FFMPEG,
                                         [fake.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                                          fake.CAP_PROP_READ_TIMEOUT_MSEC, 3000]))

    def test_frame_expiry_recovery_and_two_consumers(self):
        first = self.store.publish(JPEG, 100, 100, captured_at=1000)
        self.assertIs(self.store.get("latest", now_ms=1050), self.store.get(first.frame_id, now_ms=1050))
        with self.assertRaises(FrameError) as expired:
            self.store.get(first.frame_id, now_ms=1101)
        self.assertEqual(expired.exception.code, "FRAME_EXPIRED")
        self.store.mark_unavailable()
        with self.assertRaises(FrameError) as absent:
            self.store.get("latest", now_ms=1050)
        self.assertEqual(absent.exception.code, "CAMERA_UNAVAILABLE")
        second = self.store.publish(JPEG, 100, 100, captured_at=1200)
        self.assertNotEqual(first.frame_id, second.frame_id)

    def test_coordinate_restore_with_crop_scale_pad_and_perspective(self):
        transform = ImageTransform(100, 100, crop_x=5, crop_y=7,
                                   scale_x=2, scale_y=4, pad_x=10, pad_y=8,
                                   inverse_h=(1, 0, 3, 0, 1, 4, 0, 0, 1))
        self.assertEqual(transform.to_raw(30, 48), (18, 21))

    def test_unmapped_low_confidence_and_boundary(self):
        frame = self.store.publish(JPEG, 100, 100)
        result = Detector(MockBackend(self.fixture), self.mapping).detect(frame)
        self.assertTrue(result["simulated"])
        self.assertEqual(result["targets"][0]["target_status"], "unmapped")
        self.assertIsNone(result["targets"][0]["category"])
        self.assertEqual(result["targets"][1]["target_status"], "low_confidence")
        self.assertEqual(result["coordinate_space"], "raw_camera")

    def test_no_target_missing_model_and_idempotence(self):
        self.fixture.write_text('{"boxes": []}')
        frame = self.store.publish(JPEG, 100, 100)
        service = VisionService(self.store, Detector(MockBackend(self.fixture), self.mapping))
        first = service.detect("req-1", "latest")
        self.assertEqual(first["outcome"], "no_target")
        self.assertEqual(first["frame_id"], frame.frame_id)
        self.store.publish(JPEG, 100, 100)
        self.assertIs(first, service.detect("req-1", "latest"))
        with self.assertRaises(ValueError):
            service.detect("req-1", frame.frame_id)
        with self.assertRaises(ModelError) as absent:
            Detector(UnavailableBackend(), self.mapping).detect(frame)
        self.assertEqual(absent.exception.code, "MODEL_UNAVAILABLE")
        with self.assertRaises(ModelError) as failed:
            Detector(FailedBackend("bad fixture"), self.mapping).detect(frame)
        self.assertEqual(failed.exception.code, "MODEL_LOAD_FAILED")
        unavailable = VisionService(self.store, Detector(UnavailableBackend(), self.mapping))
        with self.assertRaises(ModelError):
            unavailable.detect("failed-1", "latest")
        unavailable.detector = Detector(MockBackend(self.fixture), self.mapping)
        with self.assertRaises(ModelError):
            unavailable.detect("failed-1", "latest")

    def test_box_clips_to_raw_bounds(self):
        self.fixture.write_text(json.dumps({"boxes": [
            {"label": "paper", "confidence": 0.8, "bbox": [-10, -5, 120, 140]}]}))
        frame = self.store.publish(JPEG, 100, 100)
        target = Detector(MockBackend(self.fixture), self.mapping).detect(frame)["targets"][0]
        self.assertEqual(target["bbox"], [0.0, 0.0, 99.0, 99.0])
        self.assertEqual(target["target_status"], "ready")

    def test_http_detection_and_shared_image(self):
        # Static-image HTTP mode has no live-frame TTL; other tests cover expiry.
        self.store.max_age_ms = 0
        frame = self.store.publish(JPEG, 100, 100)
        service = VisionService(self.store, Detector(MockBackend(self.fixture), self.mapping))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        root = f"http://127.0.0.1:{server.server_port}"
        with urlopen(root + "/api/v1/ready") as response:
            ready = json.load(response)
        self.assertEqual(ready["status"], "ready")
        self.assertTrue(ready["camera_ready"])
        self.assertTrue(ready["simulated"])  # explicit mock protocol test
        with urlopen(root + "/api/v1/frames/latest/meta") as response:
            meta = json.load(response)
        self.assertEqual(meta["frame_id"], frame.frame_id)
        with urlopen(root + "/api/v1/frames/" + frame.frame_id) as response:
            self.assertEqual(response.read(), JPEG)
            self.assertEqual(response.headers["X-Frame-Id"], frame.frame_id)
        saved = collect(root, Path(self.temp.name) / "samples", "S001")
        self.assertTrue(saved.is_file())
        record = json.loads((saved.parent / "samples.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["frame_id"], frame.frame_id)
        self.assertIsNone(record["measured_x"])
        request = Request(root + "/api/v1/detect", data=json.dumps({
            "request_id": "r1", "frame_id": "latest"}).encode(),
            headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            result = json.load(response)
        self.assertEqual(result["frame_id"], frame.frame_id)
        self.assertEqual(result["request_id"], "r1")
        self.assertIn("processing_ms", result)
        self.assertLessEqual(result["captured_at"], result["inference_started_at"])
        self.assertLessEqual(result["inference_started_at"], result["inference_finished_at"])
        self.assertIsNone(result["frame_valid_until"])  # static image mode
        self.store.mark_unavailable()
        with self.assertRaises(HTTPError) as error:
            urlopen(root + "/api/v1/frames/latest/meta")
        self.assertEqual(error.exception.code, 503)
        service.detector = Detector(UnavailableBackend(), self.mapping)
        failed_request = Request(root + "/api/v1/detect", data=json.dumps({
            "request_id": "r2", "frame_id": frame.frame_id}).encode(),
            headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as model_error:
            urlopen(failed_request)
        self.assertEqual(model_error.exception.code, 503)
        self.assertEqual(json.load(model_error.exception)["code"], "MODEL_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
