import unittest
from emotion_interaction.combined_session import CombinedSession
from emotion_interaction.gesture_policy import PolicyConfig
from emotion_interaction.smile_policy import SmileConfig
from emotion_interaction.interaction_request import FIELDS


class FakeDetector:
    def __init__(self, result):
        self.result = result
        self.frames = []

    def detect(self, frame):
        self.frames.append(frame)
        return dict(self.result)


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.face = FakeDetector({"face_count":1,"status":"FACE_DETECTED","smile_score":.9,"face_box":None})
        self.hand = FakeDetector({"gesture":"like","confidence":.9,"raw_label":"thumb_up","hand_count":1})
        self.session = CombinedSession(self.face,self.hand,
            SmileConfig(confirm_frames=2), PolicyConfig(confirm_frames=2))
        self.frame = object()

    def feed(self, i, now_ms=None):
        return self.session.process(self.frame,frame_id=str(i),captured_at=1000+i*10,
                                    now_ms=1000+i*10 if now_ms is None else now_ms)

    def test_same_frame_and_independent_confirmation(self):
        self.feed(0)
        result = self.feed(1)
        self.assertTrue(all(f is self.frame for f in self.face.frames+self.hand.frames))
        self.assertEqual(result["gesture_candidate"]["gesture"],"like")
        self.assertEqual(result["smile_request"]["action"],"WAVE")
        self.assertEqual(set(result["smile_request"]),FIELDS)
        result = self.feed(2)
        self.assertIsNone(result["gesture_candidate"])
        self.assertIsNone(result["smile_request"])

    def test_no_smile_still_allows_local_gesture(self):
        self.face.result["smile_score"] = .1
        self.feed(0)
        result = self.feed(1)
        self.assertIsNone(result["smile_request"])
        self.assertIsNotNone(result["gesture_candidate"])

    def test_no_hand_still_allows_smile(self):
        self.hand.result.update(gesture=None,confidence=0,raw_label="no_hand",hand_count=0)
        self.feed(0)
        result = self.feed(1)
        self.assertIsNotNone(result["smile_request"])
        self.assertIsNone(result["gesture_candidate"])

    def test_both_skip_when_combined_processing_exceeds_frame_age(self):
        for i in range(3):
            result = self.feed(i,now_ms=2000+i*10)
            self.assertIsNone(result["smile_request"])
            self.assertIsNone(result["gesture_candidate"])


if __name__ == "__main__": unittest.main()
