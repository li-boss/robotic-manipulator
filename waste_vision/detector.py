
"""Detection pipeline. The mock backend is explicitly nonphysical test data."""
from __future__ import annotations

import json
from math import isfinite
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from camera_service.camera import Frame
from waste_vision.category_mapping import load_mapping


class ModelError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageTransform:
    """Maps model-input pixels to raw pixels through exact inverse geometry."""
    raw_width: int
    raw_height: int
    crop_x: float = 0
    crop_y: float = 0
    scale_x: float = 1
    scale_y: float = 1
    pad_x: float = 0
    pad_y: float = 0
    # Optional inverse perspective homography, row-major, from work area to raw.
    inverse_h: tuple[float, ...] | None = None

    def to_raw(self, x: float, y: float) -> tuple[float, float]:
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ValueError("invalid preprocessing scale")
        x = (x - self.pad_x) / self.scale_x + self.crop_x
        y = (y - self.pad_y) / self.scale_y + self.crop_y
        if self.inverse_h is not None:
            h = self.inverse_h
            if len(h) != 9:
                raise ValueError("inverse homography needs nine values")
            d = h[6] * x + h[7] * y + h[8]
            if abs(d) < 1e-12:
                raise ValueError("perspective point at infinity")
            x, y = (h[0] * x + h[1] * y + h[2]) / d, (h[3] * x + h[4] * y + h[5]) / d
        return x, y


class MockBackend:
    """Fixture boxes use model_input coordinates; never use for arm movement."""
    def __init__(self, fixture_path: str | Path):
        data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("boxes"), list):
            raise ModelError("MODEL_LOAD_FAILED", "invalid mock fixture")
        self.boxes = data["boxes"]
        self.transform = data.get("transform", {})
        self.is_simulated = True

    def infer(self, frame: Frame) -> list[dict]:
        return self.boxes


class UnavailableBackend:
    """Explicit boundary for real model/OM device runtime integration."""
    is_simulated = False

    def infer(self, frame: Frame) -> list[dict]:
        raise ModelError("MODEL_UNAVAILABLE", "no verified real model backend configured")


class FailedBackend:
    is_simulated = False

    def __init__(self, reason: str):
        self.reason = reason

    def infer(self, frame: Frame) -> list[dict]:
        raise ModelError("MODEL_LOAD_FAILED", self.reason)


class Detector:
    def __init__(self, backend, mapping_path: str | Path, threshold: float = 0.5,
                 top_face_locator=None):
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be within [0,1]")
        self.backend, self.mapping = backend, load_mapping(mapping_path)
        self.threshold = threshold
        self.top_face_locator = top_face_locator

    def detect(self, frame: Frame) -> dict:
        started = perf_counter()
        try:
            raw_boxes = self.backend.infer(frame)
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError("INFERENCE_FAILED", str(exc)) from exc
        inference_ms = (perf_counter() - started) * 1000
        box_space = getattr(self.backend, "box_coordinate_space", "model_input")
        if box_space not in {"model_input", "raw_camera"}:
            raise ModelError("INFERENCE_FAILED", f"unsupported backend box space: {box_space}")
        transform = (ImageTransform(frame.image_width, frame.image_height,
                                    **getattr(self.backend, "transform", {}))
                     if box_space == "model_input" else None)
        targets = []
        for index, item in enumerate(raw_boxes, 1):
            try:
                x1, y1, x2, y2 = map(float, item["bbox"])
                if not all(isfinite(v) for v in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
                    raise ValueError("invalid or nonpositive box")
                if box_space == "raw_camera":
                    # Ultralytics has already undone letterboxing; never transform twice.
                    if not (0 <= x1 < x2 <= frame.image_width and
                            0 <= y1 < y2 <= frame.image_height):
                        raise ValueError("raw_camera box outside original image")
                    left, top, right, bottom = x1, y1, x2, y2
                else:
                    corners = [transform.to_raw(x, y) for x in (x1, x2) for y in (y1, y2)]
                    left, top = min(x for x, _ in corners), min(y for _, y in corners)
                    right, bottom = max(x for x, _ in corners), max(y for _, y in corners)
                    left, top = max(0.0, left), max(0.0, top)
                    right, bottom = min(float(frame.image_width - 1), right), min(float(frame.image_height - 1), bottom)
                    if right <= left or bottom <= top:
                        continue
                confidence = float(item["confidence"])
                if not isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("invalid confidence")
                label = str(item["label"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelError("INFERENCE_FAILED", f"invalid backend box {index}: {exc}") from exc
            targets.append({"target_id": f"{frame.frame_id}-t{index}",
                            "raw_label": label,
                            "category": self.mapping.get(label),
                            "confidence": confidence,
                            "bbox": [left, top, right, bottom],
                            "center_x": (left + right) / 2,
                            "center_y": (top + bottom) / 2,
                            "target_status": "low_confidence" if confidence < self.threshold else
                                             "unmapped" if self.mapping.get(label) is None else "ready"})
        outcome = "no_target" if not targets else "targets"
        result = {**frame.metadata(), "outcome": outcome, "targets": targets,
                  "inference_ms": round(inference_ms, 3),
                  "simulated": bool(getattr(self.backend, "is_simulated", False))}
        if self.top_face_locator is not None:
            try:
                faces, face_ms = (self.top_face_locator.locate(
                    frame.jpeg, frame.image_width, frame.image_height, targets)
                    if targets else ([], 0.0))
                if len(faces) != len(targets):
                    raise ValueError("top-face result count differs from target count")
            except Exception as exc:
                raise ModelError("TOP_FACE_FAILED", f"top-face localization failed: {exc}") from exc
            for target, face in zip(targets, faces):
                target["top_face"] = face
            result["top_face_ms"] = face_ms
        return result
