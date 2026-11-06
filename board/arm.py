"""统一舵机控制层：所有发往舵机的指令都经过这里的 angle / timing / Controller.write 校验。

默认模拟；只有 Controller(config, real=True) 才导入 Arm_Lib。AGENTS.md 要求新增控制入口
必须复用本文件，不能直接调用 Arm_Lib 写接口。"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import json
import io
import math
import os
import time


HARD_LIMITS = {1: (0, 180), 2: (0, 180), 3: (0, 180),
               4: (0, 180), 5: (90, 270), 6: (0, 180)}
READBACK_TOLERANCE_DEG = 1


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


MAX_WRITE_RETRIES = 2
WRITE_RETRY_GAP_MS = 30


def checked_call(function, *args):
    """V0.0.5 的 I2C 异常只打印消息；将任何诊断输出视为失败。"""
    diagnostics = io.StringIO()
    with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
        result = function(*args)
    if diagnostics.getvalue().strip():
        raise RuntimeError(diagnostics.getvalue().strip())
    return result


def angle(config, joint, value):
    if type(joint) is not int or joint not in HARD_LIMITS:
        raise ValueError("舵机编号必须为整数 1～6")
    limits = config.get("joint_limits", {}).get(str(joint))
    if (not isinstance(limits, list) or len(limits) != 2
            or not all(number(v) for v in limits) or limits[0] > limits[1]):
        raise ValueError(f"请先填写舵机 {joint} 的已核对范围 [最小值, 最大值]")
    low, high = HARD_LIMITS[joint]
    if (not all(int(v) == v for v in limits)
            or not low <= limits[0] <= limits[1] <= high):
        raise ValueError(f"舵机 {joint} 配置范围必须是 {low}～{high} 内的整数范围，禁止扩大硬边界")
    if not number(value) or not limits[0] <= value <= limits[1]:
        raise ValueError(f"舵机 {joint} 角度 {value!r} 不在 {limits} 内")
    if int(value) != value:
        raise ValueError("动作角度必须是整数")
    return int(value)


def timing(config):
    duration, settle = config.get("motion_ms"), config.get("settle_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or not 500 <= duration <= 10000:
        raise ValueError("本练习要求 motion_ms 为 500～10000 的整数")
    if not isinstance(settle, int) or isinstance(settle, bool) or not 100 <= settle <= 5000:
        raise ValueError("settle_ms 必须为 100～5000 的整数")
    return duration, settle


def observed_angle(config, joint, value):
    """仅校验观察到的起始读数，允许配置边缘1度偏差；返回原读数，不产生目标。"""
    # 借用目标校验核对编号和配置，容差绝不能让配置扩大硬边界。
    if type(joint) is not int or joint not in HARD_LIMITS:
        raise ValueError("舵机编号必须为整数1～6")
    limits = config.get("joint_limits", {}).get(str(joint))
    if not isinstance(limits, list) or len(limits) != 2:
        raise ValueError(f"关节{joint}未配置已核对范围")
    angle(config, joint, limits[0])
    angle(config, joint, limits[1])
    if (not number(value) or int(value) != value
            or not limits[0] - READBACK_TOLERANCE_DEG <= value <= limits[1] + READBACK_TOLERANCE_DEG):
        raise ValueError(f"关节{joint}起始读数{value!r}超出配置及1°观察容差")
    return value


class Controller:
    def __init__(self, config, real=False):
        self.config, self.real = config, real
        self.device = None
        if real:
            from Arm_Lib import Arm_Device
            self.device = Arm_Device()
            time.sleep(0.1)

    def read_report(self):
        results = []
        for joint in range(1, 7):
            try:
                value = checked_call(self.device.Arm_serial_servo_read, joint) if self.real else 90
                # 读数允许观察5号关节0～270，运动仍严格限制为90～270。
                if not number(value) or not 0 <= value <= HARD_LIMITS[joint][1]:
                    raise RuntimeError(f"无效读数：{value!r}")
                results.append(dict(id=joint, angle=value, read_ok=True, error=None,
                                    within_motion_hard_limits=HARD_LIMITS[joint][0] <= value))
            except Exception as exc:
                results.append(dict(id=joint, angle=None, read_ok=False, error=str(exc),
                                    within_motion_hard_limits=None))
            if self.real:
                time.sleep(0.02)
        return results

    def read(self):
        results = self.read_report()
        if not all(row["read_ok"] for row in results):
            raise RuntimeError(f"读取不完整，禁止记录姿态：{results}")
        return [row["angle"] for row in results]

    def write(self, pairs, hold_ms=None, retries=0):
        """hold_ms 只能缩短写入后的等待（衔接下一段），不改变角度与时间校验。
        retries 为单关节写入失败后重发同一绝对目标的次数（用户10-30：网页入口最多2次，默认0）。"""
        if self.real and self.config.get("hardware_ready") is not True:
            raise ValueError("实机未就绪，禁止下发运动指令")
        duration, settle = timing(self.config)
        if hold_ms is not None and (type(hold_ms) is not int or not 0 <= hold_ms <= duration + settle):
            raise ValueError("写入后等待时间须为0～用时+停稳之间的整数毫秒")
        if type(retries) is not int or not 0 <= retries <= MAX_WRITE_RETRIES:
            raise ValueError(f"写入重发次数须为0～{MAX_WRITE_RETRIES}的整数")
        checked = [(joint, angle(self.config, joint, value)) for joint, value in pairs]
        print(f"{'实机' if self.real else '模拟'}：{checked}，运动 {duration} ms" + ("" if hold_ms is None else f"，{hold_ms} ms 后衔接"))
        self.last_retries = []
        if self.real:
            for joint, value in checked:
                for attempt in range(retries + 1):
                    try:
                        checked_call(self.device.Arm_serial_servo_write, joint, value, duration)
                        break
                    except RuntimeError as exc:
                        if attempt == retries:
                            raise
                        # 只重发同一条已校验的绝对目标；重复下发不会让关节多走。
                        self.last_retries.append(dict(joint=joint, angle=value, attempt=attempt + 1, error=str(exc)))
                        print(f"实机：关节{joint}写入失败，{WRITE_RETRY_GAP_MS} ms 后重发同一目标（第{attempt + 1}次）")
                        time.sleep(WRITE_RETRY_GAP_MS / 1000)
                time.sleep(0.02)
            time.sleep((duration + settle if hold_ms is None else hold_ms) / 1000)


@contextmanager
def hardware_lock():
    from paths import DATA
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / ".hardware.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("设备被本项目另一进程占用；若为残留锁，请确认程序已退出、设备已停止后删除 .hardware.lock")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def now():
    return datetime.now(timezone.utc).isoformat()


def append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
