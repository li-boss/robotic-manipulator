"""单关节测试入口验证：全部假设备，不连接真实硬件。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import arm
import joint_check


def config():
    return dict(hardware_ready=True, motion_ms=1500, settle_ms=300,
                joint_limits={str(i): list(limits) for i, limits in arm.HARD_LIMITS.items()})


class Fake:
    real = True
    def __init__(self, failed_read=False, failed_write=False):
        self.calls = []
        self.failed_read, self.failed_write = failed_read, failed_write
    def read_report(self):
        return [dict(id=i, angle=None if self.failed_read else 90,
                     read_ok=not self.failed_read, error='I2C error' if self.failed_read else None,
                     within_motion_hard_limits=True) for i in range(1, 7)]
    def write(self, pairs):
        self.calls.append(pairs)
        if self.failed_write:
            raise RuntimeError('write failed')


class Tests(unittest.TestCase):
    def setUp(self):
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.folder = tempfile.TemporaryDirectory()
        self.log = Path(self.folder.name) / 'events.jsonl'
    def tearDown(self):
        self.folder.cleanup()
        self.output.__exit__(None, None, None)
    def run_check(self, cfg, joint=1, target=95, fake=None, answers=('y', 'y')):
        fake = fake or Fake()
        choices = iter(answers)
        return joint_check.test_once(cfg, joint, target, True, self.log,
                                     lambda _: next(choices), lambda *a, **k: fake)
    def test_each_joint_single_write(self):
        for joint in range(1, 7):
            fake = Fake()
            event = self.run_check(config(), joint, 95, fake)
            self.assertEqual(fake.calls, [[(joint, 95)]])
            self.assertEqual(event['observed_result'], 'normal')
    def test_all_boundaries_and_illegal_values_prevent_initialization(self):
        for joint, (low, high) in arm.HARD_LIMITS.items():
            for value in (low, high):
                fake = Fake()
                self.run_check(config(), joint, value, fake)
                self.assertEqual(fake.calls, [[(joint, value)]])
            for value in (low-1, high+1, None, True, '90', 90.5, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    joint_check.test_once(config(), joint, value, True, self.log,
                        factory=lambda *a, **k: self.fail('非法目标不能初始化'))
    def test_unconfigured_and_expanded_limits_rejected(self):
        for joint, (low, high) in arm.HARD_LIMITS.items():
            for limits in (None, [low-1, high], [low, high+1]):
                cfg = config()
                cfg['joint_limits'][str(joint)] = limits
                with self.assertRaises(ValueError):
                    joint_check.test_once(cfg, joint, 90, True, self.log,
                        factory=lambda *a, **k: self.fail('无效范围不能初始化'))
    def test_not_ready_no_init(self):
        with self.assertRaises(ValueError):
            joint_check.test_once(dict(config(), hardware_ready=False), 1, 95, True, self.log,
                factory=lambda *a, **k: self.fail('未就绪不能初始化'))
    def test_read_failure_no_write(self):
        fake = Fake(failed_read=True)
        with self.assertRaises(RuntimeError):
            self.run_check(config(), fake=fake)
        self.assertEqual(fake.calls, [])
    def test_cancel_no_write(self):
        fake = Fake()
        self.assertEqual(self.run_check(config(), fake=fake, answers=('n',))['status'], 'cancelled')
        self.assertEqual(fake.calls, [])
    def test_failure_no_retry_or_return(self):
        fake = Fake(failed_write=True)
        with self.assertRaises(RuntimeError):
            self.run_check(config(), fake=fake)
        self.assertEqual(fake.calls, [[(1, 95)]])
        event = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual((event['status'], event['retry_count']), ('aborted', 0))
    def test_initial_angle_outside_config_no_write(self):
        cfg = config()
        cfg['joint_limits']['1'] = [95, 100]
        fake = Fake()
        with self.assertRaises(ValueError):
            self.run_check(cfg, fake=fake)
        self.assertEqual(fake.calls, [])

    def test_readback_89_allows_start_but_not_target_or_config(self):
        class OffByOne(Fake):
            def read_report(self):
                rows = super().read_report()
                rows[4].update(angle=89, within_motion_hard_limits=False)
                return rows
        fake = OffByOne()
        event = self.run_check(config(), 5, 90, fake)
        self.assertEqual(event['current_angle'], 89)
        self.assertEqual(fake.calls, [[(5, 90)]])
        with self.assertRaises(ValueError):
            self.run_check(config(), 5, 89, fake)
        bad = config()
        bad['joint_limits']['5'] = [89, 250]
        with self.assertRaises(ValueError):
            self.run_check(bad, 5, 90, fake)
        with self.assertRaises(ValueError):
            arm.observed_angle(config(), 5, 88)

    def test_observation_tolerance_all_joints(self):
        for joint in range(1, 7):
            cfg = config()
            cfg['joint_limits'][str(joint)] = [100, 120]
            self.assertEqual(arm.observed_angle(cfg, joint, 99), 99)
            self.assertEqual(arm.observed_angle(cfg, joint, 121), 121)
            for value in (98, 122, None, True, '99', 99.5, float('nan')):
                with self.assertRaises(ValueError):
                    arm.observed_angle(cfg, joint, value)
            for value in (99, 121):
                with self.assertRaises(ValueError):
                    arm.angle(cfg, joint, value)


if __name__ == '__main__':
    unittest.main()
