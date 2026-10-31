import unittest
from emotion_interaction.gesture_policy import GesturePolicy, PolicyConfig
from emotion_interaction.interaction_request import (
    FIELDS, make_smile_request, request_from_gesture, validate_trigger,
)


class GesturePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = GesturePolicy(PolicyConfig(confirm_frames=3, cooldown_ms=500, release_frames=2))

    def feed(self, i, gesture="like", confidence=.9, **kwargs):
        values = dict(frame_id=str(i), captured_at=1000+i*10, now_ms=1000+i*10,
                      gesture=gesture, confidence=confidence)
        values.update(kwargs)
        return self.policy.update(**values)

    def test_confirm_and_hold(self):
        self.assertIsNone(self.feed(0))
        self.assertIsNone(self.feed(1))
        self.assertEqual(self.feed(2)["gesture"], "like")
        for i in range(3,100):
            self.assertIsNone(self.feed(i))

    def test_release_then_cooldown(self):
        for i in range(3): self.feed(i)
        self.feed(3, None); self.feed(4, None)
        for i in range(5,52): self.assertIsNone(self.feed(i))
        self.assertIsNotNone(self.feed(52))

    def test_bad_score_and_unknown(self):
        for i, score in enumerate([-.1, 1.1, float("nan"), .2, True, None]):
            self.assertIsNone(self.feed(i, confidence=score))
        self.assertIsNone(self.feed(10,"peace"))

    def test_repeated_frame_does_not_confirm(self):
        for _ in range(5): self.assertIsNone(self.feed(0))

    def test_expired_and_future(self):
        self.assertIsNone(self.feed(0, captured_at=0))
        self.assertIsNone(self.feed(1, captured_at=2000))

    def test_gap_breaks_continuity(self):
        self.feed(0); self.feed(1)
        self.assertIsNone(self.feed(100))

    def test_gesture_change_restarts(self):
        self.feed(0); self.feed(1)
        self.assertIsNone(self.feed(2,"palm"))

    def test_out_of_order_breaks_continuity(self):
        self.feed(2)
        self.assertIsNone(self.feed(1))


class ContractTests(unittest.TestCase):
    def request(self):
        return make_smile_request(.91,1000)

    def test_jjt_trigger_fields(self):
        self.assertEqual(set(self.request()), FIELDS)
        validate_trigger(self.request())

    def test_gesture_does_not_create_robot_trigger(self):
        with self.assertRaisesRegex(ValueError,"GESTURE_ACTION_NOT_CONFIGURED"):
            request_from_gesture({"gesture":"like"})
        request=self.request(); request["action"]="GRAB"
        with self.assertRaises(ValueError): validate_trigger(request)

    def test_invalid_trigger_values(self):
        for key,value in [("confidence",91),("confidence",True),("confidence",float("nan")),
                          ("action","GRAB"),("detected_at",1.0),("request_id","bad")]:
            request=self.request();request[key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): validate_trigger(request)


if __name__ == "__main__": unittest.main()
