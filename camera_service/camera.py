
"""One owner for capture, with immutable frames shared by all consumers."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import time_ns
from uuid import uuid4


class FrameError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.code, self.retryable = code, retryable
        self.details = details or {}


@dataclass(frozen=True)
class Frame:
    frame_id: str
    camera_id: str
    captured_at: int  # A computer publication time in Unix ms; not sensor exposure
    image_width: int
    image_height: int
    coordinate_space: str
    encoding: str
    jpeg: bytes
    transform_version: str = "raw-v1"

    def metadata(self) -> dict:
        return {k: getattr(self, k) for k in (
            "frame_id", "camera_id", "captured_at", "image_width",
            "image_height", "coordinate_space", "encoding", "transform_version")}


class FrameStore:
    def __init__(self, camera_id: str, capacity: int = 32, max_age_ms: int = 500):
        if capacity < 1 or max_age_ms < 0:
            raise ValueError("capacity must be positive; max_age_ms cannot be negative")
        self.camera_id, self.capacity, self.max_age_ms = camera_id, capacity, max_age_ms
        self._frames: dict[str, Frame] = {}
        self._latest: str | None = None
        self._seq = 0
        self._lock = Lock()
        self.available = False

    def publish(self, jpeg: bytes, width: int, height: int,
                captured_at: int | None = None) -> Frame:
        if not jpeg or width < 1 or height < 1:
            raise ValueError("invalid image")
        with self._lock:
            self._seq += 1
            frame = Frame(f"{self.camera_id}-{uuid4().hex[:8]}-{self._seq}",
                          self.camera_id, captured_at or time_ns() // 1_000_000,
                          width, height, "raw_camera", "jpeg", bytes(jpeg))
            self._frames[frame.frame_id] = frame
            self._latest = frame.frame_id
            self.available = True
            while len(self._frames) > self.capacity:
                del self._frames[next(iter(self._frames))]
            return frame

    def mark_unavailable(self) -> None:
        with self._lock:
            self.available = False

    def get(self, frame_id: str, now_ms: int | None = None) -> Frame:
        with self._lock:
            if frame_id == "latest":
                if not self.available or self._latest is None:
                    raise FrameError("CAMERA_UNAVAILABLE", "no live frame", True)
                frame_id = self._latest
            frame = self._frames.get(frame_id)
        if frame is None:
            raise FrameError("FRAME_NOT_FOUND", "frame is not in cache", True)
        now_ms = now_ms if now_ms is not None else time_ns() // 1_000_000
        if self.max_age_ms and now_ms - frame.captured_at > self.max_age_ms:
            raise FrameError("FRAME_EXPIRED", "capture older than limit", True)
        return frame

    def check_at_inference_finish(self, frame: Frame, started_at: int, finished_at: int) -> int:
        """Reject live results whose source became unavailable or expired during inference."""
        age_ms = finished_at - frame.captured_at
        details = {"frame_id": frame.frame_id, "camera_id": frame.camera_id,
                   "captured_at": frame.captured_at,
                   "inference_started_at": started_at,
                   "inference_finished_at": finished_at,
                   "frame_age_at_finish_ms": age_ms,
                   "max_age_ms": self.max_age_ms}
        with self._lock:
            available = self.available
        if not available:
            raise FrameError("CAMERA_UNAVAILABLE", "capture source unavailable at inference finish", True, details)
        if self.max_age_ms and age_ms > self.max_age_ms:
            raise FrameError("FRAME_EXPIRED", "capture expired during inference", True, details)
        return age_ms
