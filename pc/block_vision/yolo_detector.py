"""A's real YOLOv8n + experimental top-face plugin. No network or arm calls."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from threading import Lock
from time import perf_counter

import cv2
import numpy as np

from armlink import BlockDetection
from .base import BlockDetector
from .top_face import TopFaceLocator

ROOT = Path(__file__).resolve().parent
EXPECTED_NAMES = ('block_blue', 'block_green', 'block_red', 'block_yellow')


class YoloBlockDetector(BlockDetector):
    """Explicit plugin selection enables A's experimental top-face method.

    Only candidate quads above min_score enter the runner. Diagnostics include
    every YOLO box, including review/low-confidence results. score is the YOLO
    confidence, never a grasp success probability. No bbox-center fallback.
    """

    def __init__(self, weights=None, *, config=None, imgsz=640, conf=.25,
                 iou=.5, min_score=.5):
        if (type(imgsz) is not int or imgsz < 32 or
                any(not math.isfinite(v) or not 0 < v < 1 for v in (conf, iou)) or
                not math.isfinite(min_score) or not 0 <= min_score <= 1):
            raise ValueError('invalid image size or detection thresholds')
        self.weights = Path(weights or os.environ.get('BLOCK_VISION_WEIGHTS') or
                            ROOT / 'weights/best.pt').resolve()
        if not self.weights.is_file():
            raise FileNotFoundError(f'formal weights missing: {self.weights}')
        self.locator = TopFaceLocator(config or os.environ.get('BLOCK_VISION_HSV_CONFIG') or
                                      ROOT / 'a_top_face_hsv.json')
        os.environ.setdefault('YOLO_CONFIG_DIR', str(ROOT / 'weights/ultralytics_cache'))
        from ultralytics import YOLO
        self.model = YOLO(str(self.weights))
        names = tuple(self.model.names[i] for i in range(len(self.model.names)))
        if self.model.task != 'detect' or names != EXPECTED_NAMES:
            raise ValueError(f'unexpected model task/classes: {self.model.task}, {names}')
        self.model_sha256 = hashlib.sha256(self.weights.read_bytes()).hexdigest()
        self.imgsz, self.conf, self.iou, self.min_score = imgsz, conf, iou, min_score
        self._lock = Lock()
        self.last_result = None
        start = perf_counter()
        # Warmup is internal; no detections are published or cached as a frame.
        self._predict(np.zeros((imgsz, imgsz, 3), np.uint8))
        self.warmup_ms = round((perf_counter() - start) * 1000, 3)

    def _predict(self, image):
        return self.model.predict(source=image, imgsz=self.imgsz, conf=self.conf,
                                  iou=self.iou, device='cpu', verbose=False)

    def analyze(self, image):
        """Analyze exactly the supplied frame; errors propagate without fallback.

        Entire model + locator operation is serialized. Caller owns frame_id and
        freshness; this plugin never fetches another image or tracks identities.
        """
        if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or
                image.ndim != 3 or image.shape[2] != 3 or image.size == 0):
            raise ValueError('expected nonempty uint8 BGR original image')
        height, width = image.shape[:2]
        with self._lock:
            self.last_result = None
            start = perf_counter()
            predictions = self._predict(image)
            if len(predictions) != 1 or tuple(predictions[0].orig_shape) != (height, width):
                raise ValueError('model output does not match original frame dimensions')
            targets = []
            for index, box in enumerate(predictions[0].boxes):
                class_id, score = int(box.cls.item()), float(box.conf.item())
                xyxy = [float(v) for v in box.xyxy[0].tolist()]
                if (class_id not in range(4) or len(xyxy) != 4 or
                        not all(math.isfinite(v) for v in (*xyxy, score)) or
                        not (0 <= xyxy[0] < xyxy[2] <= width and
                             0 <= xyxy[1] < xyxy[3] <= height and 0 <= score <= 1)):
                    raise ValueError('invalid model class, confidence or raw-camera box')
                # Ultralytics xyxy has already undone letterbox; no second scaling.
                targets.append(dict(target_index=index, raw_label=EXPECTED_NAMES[class_id],
                                    confidence=score, bbox=xyxy,
                                    center_x=(xyxy[0] + xyxy[2]) / 2,
                                    center_y=(xyxy[1] + xyxy[3]) / 2,
                                    category=None, target_status=('low_confidence'
                                        if score < self.min_score else 'unmapped')))
            backend_ms = (perf_counter() - start) * 1000
            faces, face_ms = self.locator.locate_image(image, targets)
            for target, face in zip(targets, faces):
                target['top_face'] = face
                target['plugin_output'] = ('low_confidence' if target['confidence'] < self.min_score
                                           else face['status'])
            result = dict(simulated=False, coordinate_space='raw_camera',
                          image_width=width, image_height=height, targets=targets,
                          backend_ms=round(backend_ms, 3), top_face_ms=face_ms,
                          processing_ms=round((perf_counter() - start) * 1000, 3),
                          model_sha256=self.model_sha256,
                          top_face_config_sha256=self.locator.config_sha256,
                          algorithm_version=self.locator.algorithm_version,
                          parameters=dict(imgsz=self.imgsz, conf=self.conf, nms_iou=self.iou,
                                          min_output_score=self.min_score, device='cpu'))
            self.last_result = result
            return result

    @staticmethod
    def candidates(result):
        """Adapt top-face results only. Never substitute full-block bbox or angle."""
        found = []
        for target in result['targets']:
            face = target['top_face']
            if target['plugin_output'] != 'candidate':
                continue
            point, quad = face['candidate_point'], face['quad']
            if point is None or quad is None:
                raise ValueError('candidate has no top-face center/corners')
            found.append(BlockDetection(corners=quad, center=(point['x'], point['y']),
                                        score=target['confidence'], label=target['raw_label']))
        return sorted(found, key=lambda detection: detection.score, reverse=True)

    def detect(self, image):
        return self.candidates(self.analyze(image))
