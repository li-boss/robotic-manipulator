import json
import unittest
from urllib.parse import urlsplit

from emotion_interaction.interaction_request import (
    JjtError, JjtRobot, confirm_and_execute, make_smile_request, parse_state_names,
)


def saved_state(identifier, name, base_angle):
    return {
        "id": identifier,
        "name": name,
        "angles": [base_angle, 50, 40, 40, 90, 80],
        "order": [6, 2, 3, 4, 1, 5],
        "motion_ms": 500,
        "teaching_stage": "",
        "updated_at": "test",
        "path_verified": False,
    }


class FakeResponse:
    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class FakeJjtTransport:
    """Inspect the exact urllib request without opening a socket or device."""

    def __init__(self):
        self.mode = "simulation"
        self.csrf = "csrf-token"
        self.revision = "saved-state-revision"
        self.busy = False
        self.job = None
        self.posts = []
        self.states = [
            saved_state("a" * 32, "wave_left", 80),
            saved_state("b" * 32, "wave_right", 100),
            saved_state("c" * 32, "wave_home", 90),
        ]

    def status(self):
        return {
            "mode": self.mode,
            "csrf": self.csrf,
            "busy": self.busy,
            "job": self.job,
            "saved_states": {"sha256": self.revision, "states": self.states},
        }

    def __call__(self, request, timeout):
        self.last_timeout = timeout
        path = urlsplit(request.full_url).path
        if request.get_method() == "GET" and path == "/control/status":
            return FakeResponse(self.status())
        if request.get_method() != "POST" or path != "/control/states/execute":
            raise AssertionError(f"unexpected request: {request.get_method()} {path}")
        if request.get_header("Origin") != "http://jjt.test:8080":
            raise AssertionError("JJT same-origin header missing")
        if request.get_header("Content-type") != "application/json":
            raise AssertionError("JJT JSON content type missing")
        body = json.loads(request.data.decode("utf-8"))
        if (body.get("csrf") != self.csrf or body.get("confirmed") is not True
                or body.get("expected_sha256") != self.revision):
            raise AssertionError("bad JJT payload")
        self.posts.append(body)
        self.job = {"id": body["request_id"], "status": "completed", "sent": 6, "total": 6}
        return FakeResponse({"job": self.job})


class JjtDirectTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeJjtTransport()
        self.robot = JjtRobot("http://jjt.test:8080", poll_interval_s=0,
                              open_url=self.fake)

    def test_direct_saved_state_sequence_uses_exact_jjt_payload(self):
        trigger = make_smile_request(.91, 1000, "WAVE")
        sequence = parse_state_names("wave_left,wave_right,wave_left,wave_home")
        output = []
        result = confirm_and_execute(
            self.robot, trigger, sequence,
            ask=lambda _prompt: f"EXECUTE {trigger['request_id']}",
            output=output.append,
        )
        self.assertEqual(result["decision"], "COMPLETED")
        self.assertEqual([post["record"]["name"] for post in self.fake.posts], sequence)
        self.assertEqual(len({post["request_id"] for post in self.fake.posts}), 4)
        self.assertTrue(all(len(post["request_id"]) == 32 for post in self.fake.posts))
        self.assertNotIn("csrf-token", output[0])

    def test_decline_never_posts(self):
        trigger = make_smile_request(.91, 1000)
        result = confirm_and_execute(
            self.robot, trigger, ["wave_home"],
            ask=lambda _prompt: "no", output=lambda _value: None,
        )
        self.assertEqual(result["reason"], "USER_DECLINED")
        self.assertEqual(self.fake.posts, [])

    def test_busy_missing_and_changed_revision_stop_before_post(self):
        trigger = make_smile_request(.8, 1000)
        self.fake.busy = True
        with self.assertRaisesRegex(JjtError, "JJT_BUSY"):
            self.robot.prepare(trigger, ["wave_home"])
        self.fake.busy = False
        with self.assertRaisesRegex(JjtError, "JJT_MISSING_SAVED_STATES"):
            self.robot.prepare(trigger, ["missing"])
        plan = self.robot.prepare(trigger, ["wave_home"])
        self.fake.revision = "changed"
        with self.assertRaisesRegex(JjtError, "JJT_SAVED_STATES_CHANGED"):
            self.robot.execute(plan, confirmed=True)
        self.assertEqual(self.fake.posts, [])


if __name__ == "__main__":
    unittest.main()
