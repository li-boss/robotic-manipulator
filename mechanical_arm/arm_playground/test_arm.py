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

    def execute(self, kind, name):
        self.calls.append((kind, name))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("模拟通信故障")


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

    def test_complete_preflight(self):
        config = fixture()
        config["targets"]["yellow"]["drop"][4] = 999
        with self.assertRaises(ValueError):
            arm.plan(config, "yellow")
        with self.assertRaises(ValueError):
            arm.plan(fixture(), "missing")

    def test_empty_config_cannot_move(self):
        config = json.loads((arm.ROOT / "config.json").read_text())
        with self.assertRaises(ValueError):
            arm.plan(config, "yellow")

    def test_invalid_values(self):
        for value in (None, True, float("nan"), -1, 181, 90.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                arm.angle(fixture(), 1, value)

    def task(self, controller, answers, step=False):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "events.jsonl"
            try:
                arm.run_task(controller, "yellow", step, log, lambda _: next(answers))
            except (RuntimeError, InterruptedError):
                pass
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def test_success_requires_confirmation(self):
        controller = FakeController()
        events = self.task(controller, iter(["y"]))
        self.assertEqual(len(controller.calls), 11)
        self.assertEqual(events[-1]["result"], "success")
        self.assertEqual(events[1]["status"], "awaiting_confirmation")
        self.assertEqual(len({e["task_id"] for e in events}), 1)

    def test_unknown_is_not_success(self):
        self.assertEqual(self.task(FakeController(), iter([""]))[-1]["result"], "unconfirmed")

    def test_failure_does_not_continue_or_retry(self):
        controller = FakeController(fail_at=3)
        events = self.task(controller, iter([]))
        self.assertEqual(len(controller.calls), 3)
        self.assertEqual(events[-1]["status"], "aborted")
        self.assertEqual(events[-1]["retry_count"], 0)

    def test_cancel_before_motion(self):
        controller = FakeController()
        events = self.task(controller, iter(["n"]), step=True)
        self.assertEqual(controller.calls, [])
        self.assertEqual(events[-1]["status"], "aborted")

    def test_simulation_cannot_capture(self):
        with self.assertRaises(ValueError):
            arm.main(["capture", "ready"])

    def test_lock_rejects_concurrent_access(self):
        previous = arm.ROOT
        with tempfile.TemporaryDirectory() as directory:
            arm.ROOT = Path(directory)
            try:
                with arm.hardware_lock():
                    with self.assertRaises(RuntimeError):
                        with arm.hardware_lock():
                            self.fail("不能同时获得锁")
                self.assertFalse((arm.ROOT / ".hardware.lock").exists())
            finally:
                arm.ROOT = previous


class JogTests(unittest.TestCase):
    """jog 单关节探测。全部使用假设备，不连接真实硬件。"""

    def jog_controller(self, joint=3, low=60, high=120, reads=None,
                       fail_read_from=None, fail_write_at=None):
        from unittest.mock import Mock
        config = fixture()
        config["hardware_ready"] = True
        controller = arm.Controller(arm.probe_config(config, joint, low, high))
        controller.real = True
        controller.device = Mock()
        table = dict(reads or {})
        state = {"read": 0, "write": 0}

        def read(jid):
            if jid == joint:
                state["read"] += 1
                if fail_read_from is not None and state["read"] >= fail_read_from:
                    return None
            return table.get(jid, 90)

        def write(*_args):
            state["write"] += 1
            if fail_write_at is not None and state["write"] == fail_write_at:
                print("Arm_serial_servo_write I2C error")

        controller.device.Arm_serial_servo_read.side_effect = read
        controller.device.Arm_serial_servo_write.side_effect = write
        return controller

    def jog_session(self, answers, joint=3, low=60, high=120, step=5,
                    max_moves=arm.DEFAULT_MAX_MOVES, **controller_kwargs):
        controller = self.jog_controller(joint=joint, low=low, high=high,
                                         **controller_kwargs)
        replies = iter(answers)

        def ask(_):
            try:
                return next(replies)
            except StopIteration:
                raise EOFError()

        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / f"jog_{joint}.jsonl"
            try:
                arm.run_jog(controller, joint, low, high, step, log, ask,
                            max_moves=max_moves)
            except BaseException:
                pass
            events = [json.loads(line)
                      for line in log.read_text(encoding="utf-8").splitlines()]
        return controller, events

    def written(self, controller):
        return [call.args[1] for call in
                controller.device.Arm_serial_servo_write.call_args_list]

    def test_envelope_rejects_out_of_hard_limits(self):
        for joint, (low, high) in arm.HARD_LIMITS.items():
            for envelope in ([low - 1, high], [low, high + 1]):
                probe = arm.probe_config(fixture(), joint, envelope[0], envelope[1])
                with self.subTest(joint=joint, envelope=envelope), self.assertRaises(ValueError):
                    arm.angle(probe, joint, envelope[0])

    def test_envelope_rejects_reversed_and_bad_values(self):
        for envelope in ([120, 60], [None, 90], [True, 90], [90.5, 100], ["90", 100],
                         [float("nan"), 100], [float("inf"), 100]):
            probe = arm.probe_config(fixture(), 3, envelope[0], envelope[1])
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                arm.angle(probe, 3, envelope[0])

    def test_envelope_accepts_single_point(self):
        probe = arm.probe_config(fixture(), 3, 90, 90)
        self.assertEqual(arm.angle(probe, 3, 90), 90)

    def test_probe_config_isolates_and_preserves(self):
        config = fixture()
        config["hardware_ready"] = True
        config["joint_limits"]["5"] = [100, 260]
        probe = arm.probe_config(config, 3, 60, 120)
        self.assertEqual(probe["joint_limits"]["3"], [60, 120])
        self.assertIsNone(probe["joint_limits"]["5"])
        self.assertTrue(probe["hardware_ready"])
        self.assertEqual(config["joint_limits"]["5"], [100, 260])
        self.assertEqual(config["joint_limits"]["3"], list(arm.HARD_LIMITS[3]))

    def test_probe_config_never_raises_readiness(self):
        config = fixture()
        config["hardware_ready"] = False
        self.assertFalse(arm.probe_config(config, 3, 60, 120)["hardware_ready"])

    def test_requires_real_mode(self):
        with self.assertRaises(ValueError):
            arm.main(["jog", "3", "--envelope", "60", "120"])

    def test_requires_hardware_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = fixture()
            config["hardware_ready"] = False
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                arm.main(["--config", str(path), "--real", "jog", "3",
                          "--envelope", "60", "120"])

    def test_invalid_step_and_max_moves(self):
        for extra in (["--step", "0"], ["--step", "-5"], ["--step", "181"],
                      ["--max-moves", "0"], ["--max-moves", "-1"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                arm.main(["--real", "jog", "3", "--envelope", "60", "120"] + extra)

    def test_config_file_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = fixture()
            config["hardware_ready"] = True
            path.write_text(json.dumps(config), encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                arm.main(["--config", str(path), "--real", "jog", "3",
                          "--envelope", "10", "181"])
            self.assertEqual(path.read_bytes(), before)

    def test_first_move_must_be_absolute(self):
        controller, _ = self.jog_session(["y", "+", "-", "q"])
        self.assertEqual(self.written(controller), [])

    def test_relative_uses_last_command_not_readback(self):
        # 读回恒为 90，与指令不符；相对移动必须仍基于指令值。
        controller, events = self.jog_session(["y", "100", "+", "+", "q"], reads={3: 90})
        self.assertEqual(self.written(controller), [100, 105, 110])
        self.assertEqual([e["commanded"] for e in events if e.get("kind") == "move"],
                         [100, 105, 110])

    def test_targets_outside_envelope_never_reach_device(self):
        controller, events = self.jog_session(["y", "200", "59", "q"])
        self.assertEqual(self.written(controller), [])
        self.assertEqual(len([e for e in events if e.get("kind") == "rejected"]), 2)

    def test_garbage_input_never_moves(self):
        controller, _ = self.jog_session(["y", "x", "", "90.5", "nan", "+1.5", "--", "q"])
        self.assertEqual(self.written(controller), [])

    def test_read_failure_halts_further_writes(self):
        # 启动读是第 1 次；第一次移动后的回读是第 2 次 → 失败 → 停止下发。
        controller, events = self.jog_session(["y", "100", "+", "+", "q"], fail_read_from=2)
        self.assertEqual(self.written(controller), [100])
        self.assertFalse([e for e in events if e.get("kind") == "move"][-1]["read_ok"])

    def test_write_failure_halts_and_does_not_retry(self):
        controller, events = self.jog_session(["y", "100", "+", "+", "q"], fail_write_at=2)
        self.assertEqual(len(controller.device.Arm_serial_servo_write.call_args_list), 2)
        self.assertEqual(events[-1]["status"], "aborted")

    def test_quit_does_not_append_a_homing_move(self):
        controller, events = self.jog_session(["y", "100", "q"])
        self.assertEqual(self.written(controller), [100])
        self.assertEqual(events[-1]["last_commanded"], 100)
        self.assertEqual(events[-1]["status"], "completed")

    def test_move_budget_bounds_the_batch(self):
        controller, _ = self.jog_session(["y", "100", "+", "+", "+", "+", "q"], max_moves=3)
        self.assertEqual(len(self.written(controller)), 3)

    def test_read_and_all_commands_never_move(self):
        controller, events = self.jog_session(["y", "100", "r", "a", "q"])
        self.assertEqual(self.written(controller), [100])
        self.assertTrue([e for e in events if e.get("kind") == "read_all"])

    def test_interrupt_before_confirmation_sends_nothing(self):
        for error in (EOFError, KeyboardInterrupt):
            def ask(_):
                raise error()
            controller = self.jog_controller()
            with tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "jog_3.jsonl"
                arm.run_jog(controller, 3, 60, 120, 5, log, ask)
                events = [json.loads(line)
                          for line in log.read_text(encoding="utf-8").splitlines()]
            with self.subTest(error=error):
                self.assertEqual(self.written(controller), [])
                self.assertEqual(events[-1]["status"], "aborted")
                self.assertIsNotNone(events[-1]["ended_at"])

    def test_eof_after_start_stops_cleanly(self):
        controller, events = self.jog_session(["y"])
        self.assertEqual(self.written(controller), [])
        self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual(events[-1]["moves_attempted"], 0)

    def test_logs_session_record_and_every_move(self):
        _, events = self.jog_session(["y", "100", "q"])
        sessions = [e for e in events if "envelope" in e]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["joint"], 3)
        self.assertEqual(sessions[0]["envelope"], [60, 120])
        self.assertEqual(sessions[0]["step"], 5)
        # 开始记录写入时还没有任何移动，累计值只在末条记录上。
        self.assertEqual(sessions[0]["moves_attempted"], 0)
        self.assertEqual(sessions[0]["status"], "running")
        self.assertEqual(sessions[-1]["moves_attempted"], 1)
        self.assertEqual(sessions[-1]["status"], "completed")
        self.assertEqual(sessions[-1]["session_id"], sessions[0]["session_id"])
        self.assertIsNotNone(sessions[-1]["started_at"])
        self.assertIsNotNone(sessions[-1]["ended_at"])
        move = [e for e in events if e.get("kind") == "move"][0]
        self.assertEqual(move["commanded"], 100)
        self.assertIn("read", move)
        self.assertIn("read_ok", move)
        self.assertIn("error", move)
        self.assertIsNotNone(move["at"])


if __name__ == "__main__":
    unittest.main()
