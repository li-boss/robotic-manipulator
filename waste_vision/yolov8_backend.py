"""Explicit YOLOv8n detector for saved block weights. No mock fallback."""
from __future__ import annotations

import math
import hashlib
from pathlib import Path
from threading import Lock
from time import perf_counter

from camera_service.camera import Frame
from waste_vision.detector import ModelError

EXPECTED_NAMES = ("block_blue", "block_green", "block_red", "block_yellow")


class YoloV8nBackend:
    """One loaded Ultralytics model shared by serialized HTTP detections.

    Ultralytics Results.boxes.xyxy is already scaled back to result.orig_shape.
    Detector therefore accepts these as raw_camera pixels without another transform.
    """

    is_simulated = False
    box_coordinate_space = "raw_camera"

    def __init__(self, weights: str | Path, *, imgsz: int = 640,
                 confidence: float = 0.25, nms_iou: float = 0.5, device: str = "cpu"):
        path = Path(weights)
        if not path.is_file():
            raise ModelError("MODEL_UNAVAILABLE", f"weights missing: {path}")
        if imgsz < 32 or not 0 < confidence < 1 or not 0 < nms_iou < 1:
            raise ValueError("invalid YOLO image size or thresholds")
        if device != "cpu":
            raise ValueError("this verified backend configuration supports CPU only")
        try:
            import cv2
            import numpy as np
            from ultralytics import YOLO
            model = YOLO(str(path.resolve()))
            names = tuple(model.names[i] for i in range(4))
            if model.task != "detect" or names != EXPECTED_NAMES or len(model.names) != 4:
                raise ValueError(f"unexpected model task/classes: {model.task}, {model.names}")
        except Exception as exc:
            raise ModelError("MODEL_LOAD_FAILED", f"cannot load/validate {path}: {exc}") from exc
        self.weights = path.resolve()
        self.weights_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.imgsz = imgsz
        self.confidence = confidence
        self.nms_iou = nms_iou
        self.device = device
        self._cv2, self._np, self._model = cv2, np, model
        self._model_lock = Lock()
        self.ready = False
        self.warmup_ms: float | None = None

    def warmup(self) -> float:
        """Initialize the lazy predictor before serving requests; publish no frame or target."""
        image = self._np.zeros((self.imgsz, self.imgsz, 3), dtype=self._np.uint8)
        started = perf_counter()
        try:
            with self._model_lock:
                results = self._model.predict(source=image, imgsz=self.imgsz,
                                              conf=self.confidence, iou=self.nms_iou,
                                              device=self.device, verbose=False)
            if len(results) != 1 or tuple(results[0].orig_shape) != image.shape[:2]:
                raise ValueError("warmup output shape mismatch")
        except Exception as exc:
            self.ready = False
            raise ModelError("MODEL_WARMUP_FAILED", f"YOLO predictor warmup failed: {exc}") from exc
        self.warmup_ms = round((perf_counter() - started) * 1000, 3)
        self.ready = True
        return self.warmup_ms

    def infer(self, frame: Frame) -> list[dict]:
        if frame.coordinate_space != "raw_camera" or frame.encoding != "jpeg":
            raise ModelError("INFERENCE_FAILED", "YOLO requires raw_camera JPEG frames")
        image = self._cv2.imdecode(self._np.frombuffer(frame.jpeg, dtype=self._np.uint8),
                                   self._cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (frame.image_height, frame.image_width):
            raise ModelError("INFERENCE_FAILED", "JPEG decode failed or frame dimensions differ")
        try:
            # Ultralytics predictor keeps mutable internal state; do not call it concurrently.
            with self._model_lock:
                results = self._model.predict(source=image, imgsz=self.imgsz,
                                              conf=self.confidence, iou=self.nms_iou,
                                              device=self.device, verbose=False)
            if len(results) != 1 or tuple(results[0].orig_shape) != (frame.image_height, frame.image_width):
                raise ValueError("model result is not in original image dimensions")
            rows = []
            for box in results[0].boxes:
                cls = int(box.cls.item())
                if cls not in range(4):
                    raise ValueError(f"unexpected class id {cls}")
                xyxy = [float(v) for v in box.xyxy[0].tolist()]
                conf = float(box.conf.item())
                if not all(math.isfinite(v) for v in (*xyxy, conf)):
                    raise ValueError("non-finite model output")
                if not (0 <= xyxy[0] < xyxy[2] <= frame.image_width and
                        0 <= xyxy[1] < xyxy[3] <= frame.image_height and 0 <= conf <= 1):
                    raise ValueError(f"model box outside original image: {xyxy}")
                rows.append({"label": EXPECTED_NAMES[cls], "confidence": conf, "bbox": xyxy})
            return rows
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError("INFERENCE_FAILED", f"YOLO inference failed: {exc}") from exc
