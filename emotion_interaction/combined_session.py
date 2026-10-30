"""Same-frame independent policies; never merges gesture into the smile wire protocol."""
import time
from emotion_interaction.gesture_policy import GesturePolicy, PolicyConfig
from emotion_interaction.smile_policy import SmilePolicy, SmileConfig


class CombinedSession:
    def __init__(self, smile_detector, gesture_detector, smile_config=SmileConfig(), gesture_config=PolicyConfig()):
        self.smile_detector, self.gesture_detector = smile_detector, gesture_detector
        self.smile_policy, self.gesture_policy = SmilePolicy(smile_config), GesturePolicy(gesture_config)

    def process(self, frame, *, frame_id, captured_at, now_ms=None):
        # Both detectors see the unannotated frame; draw only after this method.
        smile = self.smile_detector.detect(frame)
        gesture = self.gesture_detector.detect(frame)
        completed_at = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        request = self.smile_policy.update(frame_id=frame_id, captured_at=captured_at,
                                          now_ms=completed_at, evidence=smile)
        candidate = self.gesture_policy.update(frame_id=frame_id, captured_at=captured_at,
            now_ms=completed_at, gesture=gesture["gesture"], confidence=gesture["confidence"])
        return {"frame_id": frame_id, "captured_at": captured_at,
                "processed_at": completed_at, "smile": smile, "gesture": gesture,
                "smile_request": request, "gesture_candidate": candidate, "transport": "NONE"}
