"""Internal candidates only. These objects are NOT v1 robot requests."""
import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyConfig:
    threshold: float = 0.8
    confirm_frames: int = 5
    cooldown_ms: int = 5000
    max_frame_age_ms: int = 500
    release_frames: int = 3


class GesturePolicy:
    def __init__(self, config=PolicyConfig()):
        if not 0 <= config.threshold <= 1 or min(config.confirm_frames, config.release_frames) < 1:
            raise ValueError("INVALID_POLICY_CONFIG")
        if min(config.cooldown_ms, config.max_frame_age_ms) < 0:
            raise ValueError("INVALID_POLICY_CONFIG")
        self.config = config
        self.candidate = None
        self.count = self.release = 0
        self.latched = None
        self.last_trigger = None
        self.last_capture = None
        self.last_now = None
        self.seen = set()
        self.recent_ids = deque()

    def update(self, *, frame_id, captured_at, now_ms, gesture, confidence):
        # Unix milliseconds are used at the boundary; time regressions fail closed.
        if self.last_now is not None and now_ms < self.last_now:
            self.candidate, self.count = None, 0
            return None
        self.last_now = now_ms
        if (not frame_id or frame_id in self.seen or captured_at > now_ms
                or now_ms - captured_at > self.config.max_frame_age_ms
                or (self.last_capture is not None and captured_at <= self.last_capture)):
            self.candidate, self.count = None, 0
            return None
        if self.last_capture is not None and captured_at - self.last_capture > self.config.max_frame_age_ms:
            self.candidate, self.count = None, 0
        self.seen.add(frame_id)
        self.recent_ids.append(frame_id)
        if len(self.recent_ids) > 2048:
            self.seen.remove(self.recent_ids.popleft())
        self.last_capture = captured_at
        valid = (gesture in {"fist", "like", "ok", "palm"}
                 and type(confidence) in (int, float) and math.isfinite(confidence)
                 and self.config.threshold <= confidence <= 1)
        if not valid:
            self.candidate, self.count = None, 0
            self.release += 1
            if self.release >= self.config.release_frames:
                self.latched = None
            return None
        self.release = 0
        if self.latched == gesture:
            return None
        if self.candidate == gesture:
            self.count += 1
        else:
            self.candidate, self.count = gesture, 1
        if self.count < self.config.confirm_frames:
            return None
        if self.last_trigger is not None and now_ms - self.last_trigger < self.config.cooldown_ms:
            return None
        self.latched, self.last_trigger = gesture, now_ms
        self.candidate, self.count = None, 0
        return {"gesture": gesture, "confidence": confidence, "frame_id": frame_id,
                "occurred_at": captured_at, "status": "LOCAL_CONFIRMED",
                "reason": "GESTURE_NOT_IN_V1_CONTRACT"}
