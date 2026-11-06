"""电脑端规划 + 开发板重放复核：本机回环地址上的测试服务、合成数据、假舵机；不连接机械臂。"""
import copy
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_cad_manipulation as cad_tests
from test_manipulation import StatefulFake
import test_manipulation_guided as guided_tests
teaching_fixture = guided_tests.teaching_fixture
from manipulation_geometry import build_plan
from manipulation_reference import build_reference
from manipulation_replay import verify_remote
from pc_planner import PcPlanner, PlannerUnavailable, PlanningRejected

SERVER_FILE = Path(__file__).resolve().parents[2] / 'pc/plan_server.py'


def start_server():
    spec = importlib.util.spec_from_file_location('plan_server_under_test', SERVER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.Handler.log_message = lambda *a: None
    server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, 'http://127.0.0.1:%d' % server.server_address[1]


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.arms, _, _ = cad_tests.case()
        cls.reference = build_reference(cls.cfg, cls.arms, teaching_fixture(cls.cfg))
        cls.target = cad_tests.target_at(cls.cfg, [0, 260], 0)
        cls.plan = build_plan(cls.cfg, cls.arms, cls.target, False, reference=cls.reference)

    def verify(self, steps):
        return verify_remote(self.cfg, self.arms, self.target, False, self.reference, steps)

    def test_replay_reproduces_the_local_plan(self):
        checked = self.verify(copy.deepcopy(self.plan['steps']))
        for key in ('steps', 'initial_angles', 'duration_ms', 'target', 'poses', 'block_release_pose', 'calibration_version'):
            self.assertEqual(checked[key], self.plan[key], key)

    def test_changed_move_target_is_rejected(self):
        index = next(i for i, s in enumerate(self.plan['steps']) if s['label'] == '接近抓取')
        for delta in (-6, 6):
            steps = copy.deepcopy(self.plan['steps'])
            steps[index]['joints'][0][1] += delta
            with self.assertRaises(ValueError):
                self.verify(steps)

    def test_changed_gripper_or_missing_stage_is_rejected(self):
        steps = copy.deepcopy(self.plan['steps'])
        grip = next(s for s in steps if s['label'] == '夹持方块')
        grip['joints'][0][1] -= 5
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.verify(steps)
        with self.assertRaisesRegex(ValueError, '缺少阶段'):
            self.verify([s for s in copy.deepcopy(self.plan['steps']) if s['label'] != '抬升方块'])

    def test_malformed_or_out_of_range_values_are_rejected(self):
        for bad in ([[1, True]], [[1, 90.0]], [[1, 90], [1, 91]], [[7, 90]], [[2, 181]], []):
            steps = copy.deepcopy(self.plan['steps'])
            steps[1]['joints'] = bad
            with self.assertRaises(ValueError, msg=str(bad)):
                self.verify(steps)
        with self.assertRaises(ValueError):
            self.verify([])

    def test_blend_flags_are_recomputed_on_board(self):
        steps = copy.deepcopy(self.plan['steps'])
        for s in steps:
            s['blend_next_ms'] = 2000
        checked = self.verify(steps)
        self.assertEqual(checked['steps'], self.plan['steps'])


    def test_parallel_and_sequential_checks_agree(self):
        one = verify_remote(self.cfg, self.arms, self.target, False, self.reference, copy.deepcopy(self.plan['steps']), processes=1)
        three = verify_remote(self.cfg, self.arms, self.target, False, self.reference, copy.deepcopy(self.plan['steps']), processes=3)
        for key in ('steps', 'duration_ms', 'poses', 'block_release_pose'):
            self.assertEqual(one[key], three[key], key)

    def test_collision_found_in_worker_process_rejects_plan(self):
        def collide(robot, values, carried=None, margin=0):
            raise ValueError('测试碰撞')
        for processes in (1, 3):
            with patch('manipulation_collision.check_samples', collide):
                with self.assertRaisesRegex(ValueError, '同步扫掠未通过：测试碰撞'):
                    self.verify_with(processes)

    def verify_with(self, processes):
        return verify_remote(self.cfg, self.arms, self.target, False, self.reference, copy.deepcopy(self.plan['steps']), processes=processes)

    def test_failed_blend_check_falls_back_to_settled_step(self):
        import manipulation_replay
        original = manipulation_replay.run_jobs
        def blend_fails(robot, jobs, processes=None):
            failures = original(robot, jobs, processes)
            return ['测试切角' if job['kind'] == 'blend' else error for job, error in zip(jobs, failures)]
        self.assertTrue(any(s.get('blend_next_ms') for s in self.plan['steps']))
        with patch.object(manipulation_replay, 'run_jobs', blend_fails):
            checked = self.verify_with(1)
        self.assertFalse(any(s.get('blend_next_ms') for s in checked['steps']))
        self.assertEqual(checked['duration_ms'], sum(s['motion_ms'] + s['settle_ms'] for s in checked['steps']))


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_server()
        cls.cfg, cls.arms, _, _ = cad_tests.case()
        cls.reference = build_reference(cls.cfg, cls.arms, teaching_fixture(cls.cfg))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health_and_plan_round_trip(self):
        client = PcPlanner(self.url)
        self.assertTrue(client.health()['hardware'] is False)
        target = cad_tests.target_at(self.cfg, [0, 260], 0)
        steps, info = client.plan(self.cfg, self.arms, target, False, self.reference)
        self.assertEqual(steps, build_plan(self.cfg, self.arms, target, False, reference=self.reference)['steps'])
        self.assertIn('round_trip_seconds', info)

    def test_version_mismatch_and_absent_service_fall_back(self):
        client = PcPlanner(self.url)
        client.digest = '0' * 64
        with self.assertRaisesRegex(PlannerUnavailable, '不一致'):
            client.plan(self.cfg, self.arms, cad_tests.target_at(self.cfg, [0, 260], 0), False, self.reference)
        closed = PcPlanner('http://127.0.0.1:9', health_timeout=.5)
        with self.assertRaises(PlannerUnavailable):
            closed.plan(self.cfg, self.arms, {}, False, self.reference)

    def test_planning_failure_is_reported_not_retried(self):
        target = cad_tests.target_at(self.cfg, [0, 260], 0)
        target['block_size_id'] = 'other'
        with self.assertRaises(PlanningRejected):
            PcPlanner(self.url).plan(self.cfg, self.arms, target, False, self.reference)


class TaskTests(guided_tests.GuidedTaskTests):
    """沿用九点任务测试的假设备环境，只验证计算来源切换；从不下发运动。"""

    def setUp(self):
        super().setUp()
        self.ros_calls = []
        self.task.planner = SimpleNamespace(request=lambda request: self.ros_calls.append(request) or (_ for _ in ()).throw(AssertionError('不应请求ROS2')))

    def preview(self):
        target = self.prepare()
        return self.task.preview(dict(target=target, scene_confirmed=True, dry_run=False))

    def test_pc_plan_is_verified_and_used_without_ros_recompute(self):
        server, url = start_server()
        try:
            self.task.pc_planner = PcPlanner(url)
            self.preview()
        finally:
            server.shutdown()
            server.server_close()
        plan = self.c.pending['parameters']['plan']
        self.assertEqual(plan['planner']['source'], 'pc')
        self.assertTrue(plan['planner']['verified_on_board'])
        self.assertEqual(self.ros_calls, [])
        self.assertEqual(StatefulFake.calls, [])

    def fallback(self, fake):
        self.task.pc_planner = fake
        self.task.planner = None
        self.preview()
        plan = self.c.pending['parameters']['plan']
        self.assertEqual(plan['planner']['source'], 'board')
        self.assertEqual(StatefulFake.calls, [])
        return plan

    def test_unavailable_pc_falls_back_to_board(self):
        plan = self.fallback(PcPlanner('http://127.0.0.1:9', health_timeout=.5))
        self.assertIn('无法连接', plan['planner']['reason'])

    def test_plan_failing_board_replay_is_discarded(self):
        def tampered(cfg, arms, target, require_path, reference):
            steps = copy.deepcopy(build_plan(cfg, arms, target, require_path, reference=reference)['steps'])
            steps[1]['joints'][0][1] += 6
            return steps, dict(round_trip_seconds=0)
        plan = self.fallback(SimpleNamespace(plan=tampered))
        self.assertIn('复核', plan['planner']['reason'])
        self.assertNotEqual(plan['steps'][1]['joints'][0][1], None)

    def test_pc_rejection_is_final_and_creates_no_plan(self):
        def rejected(*args):
            raise PlanningRejected('接近抓取：测试拒绝')
        self.task.pc_planner = SimpleNamespace(plan=rejected)
        with self.assertRaisesRegex(ValueError, '电脑端规划未通过'):
            self.preview()
        self.assertIsNone(self.c.pending)
        self.assertEqual(StatefulFake.calls, [])


# 只运行本文件新增的任务测试，不重复继承来的九点任务测试。
for _name in [n for n in dir(guided_tests.GuidedTaskTests) if n.startswith('test_')]:
    if getattr(TaskTests, _name) is getattr(guided_tests.GuidedTaskTests, _name):
        setattr(TaskTests, _name, None)


class FastRejectTests(unittest.TestCase):
    def test_unreachable_grasp_is_rejected_quickly_with_distance(self):
        cfg, arms, _, _ = cad_tests.case()
        reference = build_reference(cfg, arms, teaching_fixture(cfg))
        from manipulation_guided import GuidedSearch
        target = cad_tests.target_at(cfg, [0, 260], 0)
        with patch.object(GuidedSearch, 'grasp_endpoints', return_value=([], ['CAD连杆、相机或夹爪包络接近桌面'])), \
                patch.object(GuidedSearch, 'move', side_effect=AssertionError('预检失败后不应搜索路径')):
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, '距底座约260毫米.*快速预检'):
                build_plan(cfg, arms, target, False, reference=reference)
        self.assertLess(time.monotonic() - started, 5)

    def test_precheck_never_rejects_a_plan_the_search_accepts(self):
        cfg, arms, _, _ = cad_tests.case()
        reference = build_reference(cfg, arms, teaching_fixture(cfg))
        from manipulation_guided import GuidedSearch
        from manipulation_geometry import locate
        for xy in ([0, 245], [0, 260], [-15, 270], [20, 250]):
            target = cad_tests.target_at(cfg, xy, 0)
            try:
                build_plan(cfg, arms, target, False, reference=reference)
            except ValueError:
                continue
            search = GuidedSearch(cfg, arms, reference)
            search.located = locate(cfg, target)
            self.assertTrue(search.grasp_endpoints(search.located, first=True)[0], xy)


if __name__ == '__main__':
    unittest.main()
