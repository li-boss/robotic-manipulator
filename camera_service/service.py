"""Standalone A service: one capture owner, detection for C, JPEG frames for D."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from time import perf_counter, time_ns
from urllib.parse import unquote, urlparse

from camera_service.camera import FrameError, FrameStore
from camera_service.frame_source import CaptureWorker
from waste_vision.detector import Detector, FailedBackend, MockBackend, ModelError, UnavailableBackend


class VisionService:
    def __init__(self, frames: FrameStore, detector: Detector, worker: CaptureWorker | None = None):
        self.frames, self.detector, self.worker = frames, detector, worker
        self._requests: dict[str, tuple[str, dict | Exception]] = {}
        self._lock = Lock()

    def detect(self, request_id: str, frame_id: str) -> dict:
        if not request_id or not frame_id:
            raise ValueError("request_id and frame_id are required")
        with self._lock:
            prior = self._requests.get(request_id)
            if prior:
                if prior[0] != frame_id:
                    raise ValueError("request_id reused with different frame_id")
                if isinstance(prior[1], Exception):
                    raise prior[1]
                return prior[1]
            started = perf_counter()
            try:
                frame = self.frames.get(frame_id)  # latest resolved exactly once
                inference_started_at = time_ns() // 1_000_000
                try:
                    result = self.detector.detect(frame)
                finally:
                    inference_finished_at = time_ns() // 1_000_000
                age_ms = self.frames.check_at_inference_finish(
                    frame, inference_started_at, inference_finished_at)
            except (FrameError, ModelError) as exc:
                self._requests[request_id] = (frame_id, exc)
                raise
            result.update(request_id=request_id,
                          inference_started_at=inference_started_at,
                          inference_finished_at=inference_finished_at,
                          frame_age_at_finish_ms=age_ms,
                          frame_valid_until=(frame.captured_at + self.frames.max_age_ms
                                             if self.frames.max_age_ms else None),
                          processing_ms=round((perf_counter() - started) * 1000, 3))
            self._requests[request_id] = (frame_id, result)
            return result

    def readiness(self) -> tuple[int, dict]:
        backend = self.detector.backend
        model_ready = not isinstance(backend, (UnavailableBackend, FailedBackend)) and bool(
            getattr(backend, "ready", True))
        try:
            frame = self.frames.get("latest")
            camera_ready, latest = True, frame.metadata()
            camera_error = None
        except FrameError as exc:
            camera_ready, latest = False, None
            camera_error = exc.code
        ready = model_ready and camera_ready
        return (200 if ready else 503), {
            "status": "ready" if ready else "not_ready",
            "model_ready": model_ready, "camera_ready": camera_ready,
            "simulated": bool(getattr(backend, "is_simulated", False)),
            "warmup_ms": getattr(backend, "warmup_ms", None),
            "model_sha256": getattr(backend, "weights_sha256", None),
            "model_parameters": ({"imgsz": backend.imgsz,
                                  "confidence": backend.confidence, "nms_iou": backend.nms_iou,
                                  "device": backend.device} if hasattr(backend, "weights_sha256") else None),
            "top_face_config_sha256": getattr(self.detector.top_face_locator, "config_sha256", None),
            "top_face_config_version": getattr(self.detector.top_face_locator, "version", None),
            "top_face_algorithm_version": getattr(self.detector.top_face_locator,
                                                  "algorithm_version", None),
            "frame_timestamp_semantics": ("computer_file_publication_time"
                                          if self.worker and self.worker.mode == "image" else
                                          "computer_decoded_frame_publication_time"),
            "upstream_exposure_timestamp_available": False,
            "capture_mode": self.worker.mode if self.worker else None,
            "latest_frame": latest,
            "camera_error": camera_error or (self.worker.error if self.worker else None),
        }


def handler_for(service: VisionService):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, value: dict):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, code: str, message: str, request_id: str = "",
                   retryable: bool = False, details: dict | None = None):
            self._json(status, {"code": code, "message": message,
                                "retryable": retryable, "request_id": request_id,
                                **(details or {})})

        def do_POST(self):
            if self.path != "/api/v1/detect":
                return self._error(404, "NOT_FOUND", "unknown endpoint")
            request_id = ""
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                request_id, frame_id = body["request_id"], body["frame_id"]
                if not isinstance(request_id, str) or not isinstance(frame_id, str):
                    raise ValueError("request_id and frame_id must be strings")
                result = service.detect(request_id, frame_id)
                return self._json(200, result)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                return self._error(400, "BAD_REQUEST", str(exc), request_id)
            except FrameError as exc:
                return self._error(410 if exc.code == "FRAME_EXPIRED" else 503,
                                   exc.code, str(exc), request_id, exc.retryable, exc.details)
            except ModelError as exc:
                return self._error(503, exc.code, str(exc), request_id, True)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/v1/ready":
                status, payload = service.readiness()
                return self._json(status, payload)
            if not path.startswith("/api/v1/frames/"):
                return self._error(404, "NOT_FOUND", "unknown endpoint")
            suffix = unquote(path[len("/api/v1/frames/"):])
            metadata = suffix.endswith("/meta")
            frame_id = suffix[:-5] if metadata else suffix
            try:
                frame = service.frames.get(frame_id)
            except FrameError as exc:
                return self._error(410 if exc.code == "FRAME_EXPIRED" else 503,
                                   exc.code, str(exc), retryable=exc.retryable)
            if metadata:
                return self._json(200, frame.metadata())
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame.jpeg)))
            self.send_header("X-Frame-Id", frame.frame_id)
            self.send_header("X-Captured-At", str(frame.captured_at))
            self.send_header("X-Camera-Id", frame.camera_id)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(frame.jpeg)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="A camera and vision service")
    parser.add_argument("--mode", choices=["image", "video", "camera"], required=True)
    parser.add_argument("--source", required=True,
                        help="image/video path, camera index, or camera HTTP MJPEG URL")
    parser.add_argument("--camera-id", default="cam1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1",
                        help="loopback or an explicit RFC1918 LAN IPv4; never a wildcard/public bind")
    parser.add_argument("--backend", choices=["none", "mock", "yolov8n"], default="none")
    parser.add_argument("--weights", help="saved .pt weights; required for yolov8n")
    parser.add_argument("--model-conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--top-face", action="store_true",
                        help="experimental colored top-face localization; raw-camera pixels")
    parser.add_argument("--top-face-config", default="configs/a_top_face_hsv.json")
    parser.add_argument("--fixture", help="JSON fixture; required for mock")
    parser.add_argument("--mapping", default="configs/a_category_mapping.json")
    parser.add_argument("--max-age-ms", type=int, default=500)
    args = parser.parse_args()
    try:
        host = ipaddress.IPv4Address(args.host)
    except ipaddress.AddressValueError:
        parser.error("--host must be an IPv4 address")
    private_lan = any(host in ipaddress.IPv4Network(network) for network in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if host != ipaddress.IPv4Address("127.0.0.1") and not private_lan:
        parser.error("--host must be 127.0.0.1 or a private LAN address; wildcard/public bind refused")
    if args.backend == "mock" and not args.fixture:
        parser.error("--fixture is required with --backend mock")
    if args.backend == "yolov8n" and not args.weights:
        parser.error("--weights is required with --backend yolov8n")
    if args.backend != "yolov8n" and args.weights:
        parser.error("--weights requires --backend yolov8n")
    if args.top_face and args.backend != "yolov8n":
        parser.error("--top-face requires --backend yolov8n")
    frames = FrameStore(args.camera_id, max_age_ms=0 if args.mode == "image" else args.max_age_ms)
    if args.backend == "mock":
        try:
            backend = MockBackend(args.fixture)
        except (OSError, ValueError, ModelError) as exc:
            backend = FailedBackend(f"mock fixture cannot load: {exc}")
    elif args.backend == "yolov8n":
        # Import the heavy inference runtime only for the explicit real backend.
        os.environ.setdefault("YOLO_CONFIG_DIR", str(Path("data/a_blocks/cache/ultralytics").resolve()))
        from waste_vision.yolov8_backend import YoloV8nBackend
        try:
            backend = YoloV8nBackend(args.weights, imgsz=args.imgsz,
                                    confidence=args.model_conf, nms_iou=args.nms_iou)
            print(f"YOLO startup warmup: {backend.warmup():.3f} ms", flush=True)
        except (ModelError, ValueError) as exc:
            parser.error(f"real model startup failed: {getattr(exc, 'code', type(exc).__name__)}: {exc}")
    else:
        backend = UnavailableBackend()
    top_face_locator = None
    if args.top_face:
        from waste_vision.top_face import TopFaceLocator
        try:
            top_face_locator = TopFaceLocator(args.top_face_config)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            parser.error(f"top-face config cannot load: {exc}")
    detector = Detector(backend, args.mapping, top_face_locator=top_face_locator)
    worker = CaptureWorker(frames, args.mode, args.source)
    worker.start()
    httpd = ThreadingHTTPServer((str(host), args.port), handler_for(VisionService(frames, detector, worker)))
    print(f"A service at http://{host}:{args.port}; mode={args.mode}; backend={args.backend}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        worker.stop()


if __name__ == "__main__":
    main()
