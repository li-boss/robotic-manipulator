import unittest
from emotion_interaction.smile_detector import smile_evidence
from emotion_interaction.smile_policy import SmileConfig, SmilePolicy
from emotion_interaction.interaction_request import FIELDS, validate_v1
from integration_tools.mocks.fake_robot import FakeRobot


class SmileEvidenceTests(unittest.TestCase):
    def test_coefficients_use_both_sides(self):
        result = smile_evidence(1, {"mouthSmileLeft": .9, "mouthSmileRight": .7})
        self.assertEqual(result["smile_score"], .7)

    def test_missing_and_nonfinite(self):
        for score in (None, float("nan"), float("inf"), -.1, 1.1, True):
            result = smile_evidence(1, {"mouthSmileLeft": score, "mouthSmileRight": .9})
            self.assertEqual(result["status"], "INVALID_BLENDSHAPES")
            self.assertEqual(result["smile_score"], 0)

    def test_no_face_and_multiple_faces(self):
        for count in (0, 2):
            self.assertEqual(smile_evidence(count, {"mouthSmileLeft":1,"mouthSmileRight":1})["smile_score"], 0)


class SmilePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SmilePolicy(SmileConfig(confirm_frames=3, release_frames=2, cooldown_ms=500))

    def feed(self, i, score=.8, count=1, **changes):
        evidence=smile_evidence(count,{"mouthSmileLeft":score,"mouthSmileRight":score})
        values=dict(frame_id=str(i),captured_at=1000+i*10,now_ms=1000+i*10,evidence=evidence)
        values.update(changes)
        return self.policy.update(**values)

    def confirm(self):
        self.assertIsNone(self.feed(0))
        self.assertIsNone(self.feed(1))
        return self.feed(2)

    def test_exact_v1_and_conservative_score(self):
        self.feed(0,.7); self.feed(1,.9)
        request=self.feed(2,.8)
        self.assertEqual(set(request),FIELDS)
        self.assertEqual(request["label"],"smile")
        self.assertEqual(request["confidence"],.7)
        self.assertEqual(request["expires_at"]-request["occurred_at"],1000)
        validate_v1(request)

    def test_hold_does_not_repeat(self):
        self.assertIsNotNone(self.confirm())
        for i in range(3,200): self.assertIsNone(self.feed(i))

    def test_release_cooldown_new_id(self):
        first=self.confirm()
        self.feed(3,.1); self.feed(4,.1)
        for i in range(5,52): self.assertIsNone(self.feed(i))
        second=self.feed(52)
        self.assertNotEqual(first["interaction_id"],second["interaction_id"])

    def test_brief_low_one_sided_and_absent(self):
        self.feed(0)
        self.assertIsNone(self.feed(1,.1))
        self.assertIsNone(self.feed(2))
        self.assertIsNone(self.feed(3,count=0))
        one_sided=smile_evidence(1,{"mouthSmileLeft":.9,"mouthSmileRight":.1})
        self.assertIsNone(self.feed(4,evidence=one_sided))

    def test_repeated_expired_future_and_out_of_order(self):
        for _ in range(5): self.assertIsNone(self.feed(0))
        self.assertIsNone(self.feed(1,captured_at=0))
        self.assertIsNone(self.feed(2,captured_at=5000))
        self.feed(10)
        self.assertIsNone(self.feed(9))

    def test_gap_and_multiple_faces_interrupt(self):
        self.feed(0); self.feed(1)
        self.assertIsNone(self.feed(100))
        self.assertIsNone(self.feed(101,count=2))
        self.assertIsNone(self.feed(102))

    def test_changed_face_position_restarts_confirmation(self):
        a={**smile_evidence(1,{"mouthSmileLeft":.9,"mouthSmileRight":.9}),"face_box":[0,0,.2,.2]}
        b={**a,"face_box":[.7,.7,.9,.9]}
        self.feed(0,evidence=a); self.feed(1,evidence=a)
        self.assertIsNone(self.feed(2,evidence=b))

    def test_expired_before_creation(self):
        self.policy=SmilePolicy(SmileConfig(confirm_frames=1,ttl_ms=10))
        self.assertIsNone(self.feed(0,now_ms=1010))

    def test_invalid_config(self):
        for kwargs in ({"threshold":0},{"threshold":float("nan")},{"confirm_frames":0},
                       {"release_frames":True},{"ttl_ms":0},{"cooldown_ms":-1},{"requested_action":"GRAB"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): SmilePolicy(SmileConfig(**kwargs))

    def test_mock_original_request_idle_and_blocked(self):
        request=self.confirm()
        for state in ("IDLE","BUSY","HOLDING","FAULT","STOPPING","STOPPED"):
            robot=FakeRobot(state)
            result=robot.interact(request,request["occurred_at"])
            self.assertEqual(result["decision"],"ACCEPTED" if state=="IDLE" else "REJECTED")
            robot.interact(request,request["occurred_at"]+1)
            self.assertEqual(len(robot.actions),1 if state=="IDLE" else 0)


if __name__ == "__main__": unittest.main()
