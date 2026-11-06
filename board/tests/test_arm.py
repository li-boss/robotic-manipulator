import json
from pathlib import Path
import tempfile
import unittest

import arm


def fixture():
    # 仅用于软件测试，不是实机可用参数。
    return dict(motion_ms=1500, settle_ms=300,
                joint_limits={str(i): list(bounds) for i, bounds in arm.HARD_LIMITS.items()},
                gripper={"open": 60, "closed": 120},
                poses={key: [90] * 5 for key in ("ready", "pickup_above", "pickup")},
                targets={"yellow": {"above": [90] * 5, "drop": [90] * 5}})


class FakeController:
    real = True

    def __init__(self, fail_at=None):
        self.config = fixture()
        self.calls = []
        self.fail_at = fail_at



class Tests(unittest.TestCase):
    def test_printed_i2c_error_stops_batch(self):
        from unittest.mock import Mock
        controller = arm.Controller(fixture())
        controller.real = True
        controller.config["hardware_ready"] = True
        controller.device = Mock()
        controller.device.Arm_serial_servo_write.side_effect = lambda *args: print("I2C error")
        with self.assertRaisesRegex(RuntimeError, "I2C error"):
            controller.write([(6, 90), (1, 90)])
        self.assertEqual(controller.device.Arm_serial_servo_write.call_count, 1)

    def test_write_retry_resends_same_target_then_continues(self):
        from unittest.mock import Mock, patch
        controller = arm.Controller(dict(fixture(), hardware_ready=True))
        controller.real = True
        controller.device = Mock()
        outcomes = iter(["I2C error", "", "", ""])
        controller.device.Arm_serial_servo_write.side_effect = lambda *args: print(next(outcomes), end="")
        with patch("arm.time.sleep"):
            controller.write([(2, 53), (4, 38)], retries=2)
        calls = [c.args for c in controller.device.Arm_serial_servo_write.call_args_list]
        self.assertEqual(calls, [(2, 53, 1500), (2, 53, 1500), (4, 38, 1500)])
        self.assertEqual(controller.last_retries, [dict(joint=2, angle=53, attempt=1, error="I2C error")])

    def test_write_retry_exhausted_stops_batch(self):
        from unittest.mock import Mock, patch
        controller = arm.Controller(dict(fixture(), hardware_ready=True))
        controller.real = True
        controller.device = Mock()
        controller.device.Arm_serial_servo_write.side_effect = lambda *args: print("I2C error")
        with patch("arm.time.sleep"), self.assertRaisesRegex(RuntimeError, "I2C error"):
            controller.write([(2, 53), (4, 38)], retries=2)
        self.assertEqual([c.args for c in controller.device.Arm_serial_servo_write.call_args_list], [(2, 53, 1500)] * 3)
        for bad in (-1, 3, 1.0, True):
            with self.assertRaises(ValueError):
                controller.write([(1, 90)], retries=bad)

    def test_partial_reads_are_reported_without_motion(self):
        from unittest.mock import Mock
        controller = arm.Controller(fixture())
        controller.real = True
        controller.device = Mock()
        controller.device.Arm_serial_servo_read.side_effect = [90, None, 90, 90, 45, 90]
        rows = controller.read_report()
        self.assertEqual(len(rows), 6)
        self.assertFalse(rows[1]["read_ok"])
        self.assertIsNone(rows[1]["angle"])
        self.assertTrue(rows[4]["read_ok"])
        self.assertFalse(rows[4]["within_motion_hard_limits"])
        controller.device.Arm_serial_servo_write.assert_not_called()

    def test_hard_boundaries_for_all_joints(self):
        for joint, (low, high) in arm.HARD_LIMITS.items():
            for value in (low, high):
                self.assertEqual(arm.angle(fixture(), joint, value), value)
            for value in (low - 1, high + 1):
                with self.subTest(joint=joint, value=value), self.assertRaises(ValueError):
                    arm.angle(fixture(), joint, value)

    def test_config_cannot_expand_hard_limits(self):
        for joint, (low, high) in arm.HARD_LIMITS.items():
            for limits in ([low - 1, high], [low, high + 1], [low + 0.5, high]):
                config = fixture()
                config["joint_limits"][str(joint)] = limits
                with self.subTest(joint=joint, limits=limits), self.assertRaises(ValueError):
                    arm.angle(config, joint, high)

    def test_invalid_joint_ids(self):
        for joint in (True, 1.0, "1", 0, 7):
            with self.subTest(joint=joint), self.assertRaises(ValueError):
                arm.angle(fixture(), joint, 90)

    def test_write_preflight_and_readiness(self):
        from unittest.mock import Mock
        controller = arm.Controller(fixture())
        controller.real = True
        controller.device = Mock()
        with self.assertRaises(ValueError):
            controller.write([(1, 90)])
        controller.config["hardware_ready"] = True
        with self.assertRaises(ValueError):
            controller.write([(1, 90), (5, 0)])
        controller.device.Arm_serial_servo_write.assert_not_called()

    def test_write_hold_only_shortens_wait(self):
        from unittest.mock import Mock, patch
        controller = arm.Controller(dict(fixture(), hardware_ready=True))
        controller.real = True
        controller.device = Mock()
        controller.device.Arm_serial_servo_write.return_value = None
        for bad in (-1, 1801, 1.5, True, "100"):
            with self.assertRaises(ValueError):
                controller.write([(1, 90)], hold_ms=bad)
        controller.device.Arm_serial_servo_write.assert_not_called()
        with patch("arm.time.sleep") as sleep:
            controller.write([(1, 90), (2, 80)], hold_ms=1200)
        self.assertEqual(sleep.call_args_list[-1].args, (1.2,))
        self.assertEqual(controller.device.Arm_serial_servo_write.call_count, 2)
        with patch("arm.time.sleep") as sleep:
            controller.write([(1, 90)])
        self.assertEqual(sleep.call_args_list[-1].args, (1.8,))



    def test_invalid_values(self):
        for value in (None, True, float("nan"), -1, 181, 90.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                arm.angle(fixture(), 1, value)

    def test_shipped_template_config_cannot_move(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "data" / "arm_config.json").read_text())
        for joint in range(1, 7):
            with self.assertRaises(ValueError):
                arm.angle(config, joint, 90)
        with self.assertRaises(ValueError):
            arm.Controller(config).write([(1, 90)])

    def test_lock_rejects_concurrent_access(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch("paths.DATA", Path(directory)):
            with arm.hardware_lock():
                with self.assertRaises(RuntimeError):
                    with arm.hardware_lock():
                        self.fail("不能同时获得锁")
            self.assertFalse((Path(directory) / ".hardware.lock").exists())


if __name__ == "__main__":
    unittest.main()
