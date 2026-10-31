"""Continuous smile confirmation producing a JJT-compatible local trigger."""
import math
from collections import deque
from dataclasses import dataclass
from emotion_interaction.interaction_request import make_smile_request

DEFAULT_SMILE_THRESHOLD = 0.3


@dataclass(frozen=True)
class SmileConfig:
    threshold: float = DEFAULT_SMILE_THRESHOLD
    confirm_frames: int = 5
    release_frames: int = 3
    cooldown_ms: int = 5000
    max_frame_age_ms: int = 500
    requested_action: str = "WAVE"


class SmilePolicy:
    def __init__(self, config=SmileConfig()):
        if type(config.threshold) not in (int, float) or not math.isfinite(config.threshold) or not 0 < config.threshold <= 1:
            raise ValueError("INVALID_SMILE_THRESHOLD")
        for name in ("confirm_frames", "release_frames"):
            if type(getattr(config, name)) is not int or getattr(config, name) < 1:
                raise ValueError("INVALID_SMILE_CONFIG")
        for name in ("cooldown_ms", "max_frame_age_ms"):
            if type(getattr(config, name)) is not int or getattr(config, name) < 0:
                raise ValueError("INVALID_SMILE_CONFIG")
        if config.requested_action not in {"WAVE", "NOD"}:
            raise ValueError("INVALID_ACTION")
        self.config = config
        self.count = self.release = 0
        self.latched = False
        self.last_capture = self.last_now = self.last_trigger = None
        self.minimum_score = 1.0
        self.seen, self.recent = set(), deque()
        self.previous_box = None

    def _reset_candidate(self):
        self.count, self.minimum_score = 0, 1.0

    def update(self, *, frame_id, captured_at, now_ms, evidence):
        if type(captured_at) is not int or type(now_ms) is not int or min(captured_at, now_ms) < 0:
            self._reset_candidate()
            return None
        if self.last_now is not None and now_ms < self.last_now:
            self._reset_candidate()
            return None
        self.last_now = now_ms
        if (not isinstance(frame_id, str) or not frame_id or frame_id in self.seen
                or not 0 <= now_ms - captured_at <= self.config.max_frame_age_ms
                or (self.last_capture is not None and captured_at <= self.last_capture)):
            self._reset_candidate()
            return None
        if self.last_capture is not None and captured_at - self.last_capture > self.config.max_frame_age_ms:
            self._reset_candidate()
        self.last_capture = captured_at
        self.seen.add(frame_id)
        self.recent.append(frame_id)
        if len(self.recent) > 2048:
            self.seen.remove(self.recent.popleft())
        box = evidence.get("face_box")
        if box is not None and self.previous_box is not None and self._iou(box, self.previous_box) < .3:
            self._reset_candidate()
        self.previous_box = box
        score = evidence.get("smile_score")
        valid = (evidence.get("face_count") == 1 and evidence.get("status") == "FACE_DETECTED"
                 and type(score) in (int, float) and math.isfinite(score)
                 and self.config.threshold <= score <= 1)
        if not valid:
            self._reset_candidate()
            self.release += 1
            if self.release >= self.config.release_frames:
                self.latched = False
            return None
        self.release = 0
        if self.latched:
            return None
        self.count += 1
        self.minimum_score = min(self.minimum_score, score)
        if self.count < self.config.confirm_frames:
            return None
        if self.last_trigger is not None and now_ms - self.last_trigger < self.config.cooldown_ms:
            return None
        request = make_smile_request(self.minimum_score, captured_at,
                                     self.config.requested_action)
        self.latched, self.last_trigger = True, now_ms
        self._reset_candidate()
        return request

    @staticmethod
    def _iou(a, b):
        intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
        union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
        return intersection / union if union > 0 else 0.0
