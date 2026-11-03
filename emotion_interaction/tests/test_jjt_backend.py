"""D client -> actual B HTTP handlers -> injected fake controller. No sockets/devices."""
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from emotion_interaction.interaction_request import (
    JjtRobot, JjtError, make_smile_request, confirm_and_execute,
)
from emotion_interaction.smile_policy import SmilePolicy, SmileConfig
from emotion_interaction.tests.test_jjt_direct import FakeResponse

# The existing B modules use sibling imports. Import exactly their existing test
# fixture, whose controller and hardware lock are both fake, then restore sys.path.
BACKEND = Path(__file__).resolve().parents[2] / 'mechanical_arm' / 'sorting_demo'
original_path = sys.path.copy()
try:
    sys.path.insert(0, str(BACKEND))
    import test_control as backend_tests
    spec = importlib.util.spec_from_file_location('interaction_backend_http', BACKEND / 'server.py')
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
finally:
    sys.path[:] = original_path


class BackendInteractionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backend_tests.ControlTests()
        self.fixture.setUp()
        self.control = self.fixture.c
        self.now = 1100
        self.control.clock_ms = lambda: self.now
        # Explicit test facts, never installed as a real device state source.
        self.facts = dict(holding=False, fault=False, stopped=False)
        self.control.interaction_state_provider = lambda: dict(
            self.facts, source='injected-test-fixture', observed_at=self.now)
        for name, angle in [('wave_left', 80), ('wave_right', 100)]:
            record = dict(name=name, angles=[angle, 50, 40, 40, 90, 80],
                          order=[6, 2, 3, 4, 1, 5], motion_ms=500, teaching_stage='')
            self.control.states.save(record, self.control.states.status()['sha256'])
        self.handler = web.demo_handler(SimpleNamespace(camera=None, control=self.control))
        self.posts = []
        self.before_post = None
        self.robot = JjtRobot('http://jjt.test:8080', open_url=self.transport,
                              clock_ms=lambda: self.now, poll_interval_s=.001)

    def tearDown(self):
        self.fixture.tearDown()

    def transport(self, request, timeout):
        handler = self.handler.__new__(self.handler)
        handler.path = urlsplit(request.full_url).path
        handler.headers = Message()
        for key, value in request.header_items():
            handler.headers[key] = value
        handler.headers['Host'] = 'jjt.test:8080'
        raw = request.data or b''
        handler.headers['Content-Length'] = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        if request.get_method() == 'POST':
            self.posts.append(json.loads(raw))
            if self.before_post:
                self.before_post()
            handler.do_POST()
        else:
            handler.do_GET()
        status, value = replies[0]
        if status != 200:
            raise HTTPError(request.full_url, status, 'backend rejected', {},
                            io.BytesIO(json.dumps(value).encode()))
        return FakeResponse(value)

    def plan(self, names=('wave_left', 'wave_right'), ttl_ms=30000):
        return self.robot.prepare(make_smile_request(.9, 1000, ttl_ms=ttl_ms), names)

    def test_full_sequence_history_and_restart_without_duplicate_writes(self):
        plan = self.plan()
        result = self.robot.execute(plan, confirmed=True)
        self.assertEqual(result['decision'], 'COMMANDS_SENT')
        self.assertIs(result['motion_verified'], False)
        self.assertEqual(len(backend_tests.Fake.calls), 12)

        self.assertTrue(all(s['job']['completion_kind'] == 'commands_sent' for s in result['steps']))
        # A newer job no longer hides earlier results.
        self.control.job = {'id': 'f' * 32, 'status': 'completed'}
        self.now = 100000
        self.robot.execute(plan, confirmed=True)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(len(backend_tests.Fake.calls), 12)
        restarted = backend_tests.Control(self.fixture.path, real=True,
            factory=backend_tests.Fake, root=self.fixture.root,
            lock_factory=backend_tests.nullcontext)
        self.control = restarted
        self.handler = web.demo_handler(SimpleNamespace(camera=None, control=restarted))
        self.robot.execute(plan, confirmed=True)
        self.assertEqual(len(backend_tests.Fake.calls), 12)

    def test_confirmed_smile_to_backend_saved_state(self):
        policy = SmilePolicy(SmileConfig(confirm_frames=2))
        evidence = dict(face_count=1, status='FACE_DETECTED', smile_score=.8, face_box=None)
        self.assertIsNone(policy.update(frame_id='face-1', captured_at=1000,
                                       now_ms=1000, evidence=evidence))
        trigger = policy.update(frame_id='face-2', captured_at=1100,
                                now_ms=1100, evidence=evidence)
        result = confirm_and_execute(self.robot, trigger, ['wave_left'],
            ask=lambda _: 'EXECUTE ' + trigger['request_id'], output=lambda _: None)
        self.assertEqual(result['decision'], 'COMMANDS_SENT')
        self.assertEqual(len(backend_tests.Fake.calls), 6)

    def test_server_rejects_expired_and_future_payload_even_if_client_is_bypassed(self):
        plan = self.plan()
        step = plan['steps'][0]
        fields = dict(request_id='e' * 32, id=step['id'], record=step['record'],
                      expected_sha256=plan['expected_sha256'], confirmed=True)
        for start in (0, 2000):
            fields['interaction'] = make_smile_request(.9, start, ttl_ms=1000)
            with self.assertRaisesRegex(ValueError, 'EXPIRED|CLOCK_MISMATCH'):
                self.control.state_move(fields)
        self.assertEqual(backend_tests.Fake.calls, [])

    def test_server_rechecks_expiry_after_client_status(self):
        plan = self.plan(ttl_ms=1000)
        self.before_post = lambda: setattr(self, 'now', 2000)
        with self.assertRaisesRegex(JjtError, 'INTERACTION_EXPIRED'):
            self.robot.execute(plan, confirmed=True)
        self.assertEqual(backend_tests.Fake.calls, [])

    def test_server_rejects_holding_fault_stopped_busy_and_unknown(self):
        plan = self.plan()
        for key in ('holding', 'fault', 'stopped'):
            with self.subTest(key=key):
                self.before_post = lambda: self.facts.update({key: True})
                with self.assertRaisesRegex(JjtError, 'ROBOT_' + key.upper()):
                    self.robot.execute(plan, confirmed=True)
                self.facts[key] = False
        self.before_post = lambda: setattr(self.control, 'busy', True)
        with self.assertRaisesRegex(JjtError, '正在执行|ROBOT_BUSY'):
            self.robot.execute(plan, confirmed=True)
        self.control.busy = False
        self.before_post = lambda: setattr(self.control, 'interaction_state_provider', None)
        with self.assertRaisesRegex(JjtError, 'DEVICE_STATE_UNKNOWN'):
            self.robot.execute(plan, confirmed=True)
        self.assertEqual(backend_tests.Fake.calls, [])

    def test_worker_rechecks_state_before_first_write(self):
        plan = self.plan()
        original = self.control._start_direct
        def change_after_acceptance(*args):
            self.facts['fault'] = True
            return original(*args)
        with patch.object(self.control, '_start_direct', side_effect=change_after_acceptance):
            with self.assertRaisesRegex(JjtError, 'ROBOT_FAULT'):
                self.robot.execute(plan, confirmed=True)
        self.assertEqual(backend_tests.Fake.calls, [])

    def test_worker_stops_following_joints_when_state_changes(self):
        plan = self.plan()
        original = backend_tests.Fake.write
        def change_after_write(controller, pairs, retries=0):
            original(controller, pairs, retries=retries)
            self.facts['stopped'] = True
        with patch.object(backend_tests.Fake, 'write', change_after_write):
            with self.assertRaisesRegex(JjtError, 'ROBOT_STOPPED'):
                self.robot.execute(plan, confirmed=True)
        self.assertEqual(len(backend_tests.Fake.calls), 1)
        self.assertEqual(len(self.posts), 1)

    def test_expiry_during_execution_stops_remaining_dispatches(self):
        plan = self.plan(ttl_ms=1000)
        original = backend_tests.Fake.write
        def expire_after_write(controller, pairs, retries=0):
            original(controller, pairs, retries=retries)
            self.now = 2000
        with patch.object(backend_tests.Fake, 'write', expire_after_write):
            with self.assertRaisesRegex(JjtError, 'INTERACTION_EXPIRED'):
                self.robot.execute(plan, confirmed=True)
        self.assertEqual(len(backend_tests.Fake.calls), 1)
        self.assertEqual(len(self.posts), 1)

    def test_missing_stale_or_unavailable_real_state_fails_closed(self):
        providers = [None, lambda: dict(self.facts, source='simulation', observed_at=self.now),
                     lambda: dict(self.facts, source='test', observed_at=0),
                     lambda: dict(source='test', observed_at=self.now)]
        for provider in providers:
            self.control.interaction_state_provider = provider
            with self.subTest(provider=provider), self.assertRaisesRegex(JjtError, 'DEVICE_STATE_'):
                self.plan()
        self.assertEqual(backend_tests.Fake.calls, [])

    def test_simulation_state_is_explicit_and_job_not_found_is_read_only(self):
        self.control.real = False
        self.control.interaction_state_provider = None
        safety = self.control.status()['interaction_safety']
        self.assertEqual(safety['source'], 'simulation')
        self.assertIs(safety['allowed'], True)
        self.assertIsNone(self.robot.job('a' * 32))
        self.assertEqual(backend_tests.Fake.calls, [])


if __name__ == '__main__':
    unittest.main()
