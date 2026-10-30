"""固定点搬运练习。默认模拟；仅 --real 会导入 Arm_Lib。"""
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import json
import io
import math
import os
from pathlib import Path
import time
import uuid


ROOT = Path(__file__).resolve().parent
HARD_LIMITS = {1: (0, 180), 2: (0, 180), 3: (0, 180),
               4: (0, 180), 5: (90, 270), 6: (0, 180)}
DEFAULT_MAX_MOVES = 40
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


def get_pose(config, name):
    # ready / pickup_above / pickup / targets.yellow.above / targets.yellow.drop
    if name.startswith("targets."):
        parts = name.split(".")
        if len(parts) != 3:
            raise ValueError("目标姿态格式为 targets.yellow.above 或 targets.yellow.drop")
        values = config.get("targets", {}).get(parts[1], {}).get(parts[2])
    else:
        values = config.get("poses", {}).get(name)
    if not isinstance(values, list) or len(values) != 5:
        raise ValueError(f"姿态 {name} 尚未配置五个实测关节角度")
    return [angle(config, i, value) for i, value in enumerate(values, 1)]


def plan(config, target):
    if target not in config.get("targets", {}):
        raise ValueError(f"未知放置区域：{target}")
    steps = [
        ("准备", "pose", "ready"),
        ("张开夹爪", "gripper", "open"),
        ("到达取物点上方", "pose", "pickup_above"),
        ("下降取物", "pose", "pickup"),
        ("夹住", "gripper", "closed"),
        ("抬起", "pose", "pickup_above"),
        ("到达放置点上方", "pose", f"targets.{target}.above"),
        ("下降放置", "pose", f"targets.{target}.drop"),
        ("松开", "gripper", "open"),
        ("离开放置点", "pose", f"targets.{target}.above"),
        ("返回", "pose", "ready"),
    ]
    timing(config)
    # 在第一条运动指令之前验证完整路径，避免运行半途才发现缺配置。
    for _, kind, name in steps:
        if kind == "pose":
            get_pose(config, name)
        else:
            angle(config, 6, config.get("gripper", {}).get(name))
    return steps


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

    def execute(self, kind, name):
        if kind == "pose":
            self.write(list(enumerate(get_pose(self.config, name), 1)))
        else:
            self.write([(6, self.config["gripper"][name])])


@contextmanager
def hardware_lock():
    path = ROOT / ".hardware.lock"
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


def run_task(controller, target, step, log_path, ask=input):
    steps = plan(controller.config, target)
    event = dict(task_id=str(uuid.uuid4()), target=target,
                 mode="real" if controller.real else "simulation", started_at=now(),
                 ended_at=None, status="running", result=None, failure_reason=None,
                 retry_count=0, last_step=None)
    append_event(log_path, event)  # 无法写日志时不开始动作。
    try:
        for label, kind, name in steps:
            if step and ask(f"下一步：{label}。输入 y 执行，其他输入中止：").strip().lower() != "y":
                raise InterruptedError("用户中止逐步执行")
            event["last_step"] = label
            print(label)
            controller.execute(kind, name)
        event["status"] = "awaiting_confirmation" if controller.real else "completed"
        if controller.real:
            append_event(log_path, event)
            answer = ask("实际搬运成功了吗？输入 y=成功，n=失败，其他=未确认：").strip().lower()
            event["result"] = {"y": "success", "n": "failed"}.get(answer, "unconfirmed")
            if answer == "n":
                event["failure_reason"] = ask("失败原因（如夹空、掉落）：").strip() or "人工确认失败，未填写原因"
        else:
            event["result"] = "simulated"
        event["status"] = "completed"
    except BaseException as exc:
        event.update(status="aborted", result="failed",
                     failure_reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        event["ended_at"] = now()
        append_event(log_path, event)
        print(f"任务 {event['task_id']}：{event['result']}；记录：{log_path}")


def capture(config, path, name, values):
    if name.startswith("targets."):
        parts = name.split(".")
        if len(parts) != 3 or not parts[1] or parts[2] not in ("above", "drop"):
            raise ValueError("目标姿态使用 targets.区域名.above 或 targets.区域名.drop")
        config.setdefault("targets", {}).setdefault(parts[1], {})[parts[2]] = values[:5]
    elif name in ("ready", "pickup_above", "pickup"):
        config["poses"][name] = values[:5]
    elif name in ("gripper.open", "gripper.closed"):
        config["gripper"][name.split(".")[1]] = values[5]
    else:
        raise ValueError("未知姿态名称")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(f"已保存 {name}；请核对角度，记录姿态不代表运动路径已验证。")


def probe_config(config, joint, low, high):
    """构造单关节探测用的临时配置副本。

    只把被探测关节的 joint_limits 设为 [low, high]，其余关节一律置 None，
    因此探测期间任何多关节动作都会被 angle() 拒绝。hardware_ready 原样透传，
    绝不在这里设置——就绪状态只能由人核对后写入配置。
    """
    probe = json.loads(json.dumps(config))
    probe["joint_limits"] = {str(i): None for i in range(1, 7)}
    probe["joint_limits"][str(joint)] = [low, high]
    return probe


def read_joint(controller, joint):
    """读取单个关节，返回 read_report() 中对应的一行。读取失败不重试。"""
    for row in controller.read_report():
        if row["id"] == joint:
            return row
    raise RuntimeError(f"读取结果缺少关节 {joint}")


def parse_jog_command(raw, last_cmd, step):
    """把一行输入解析成 (kind, payload)，不做任何 I/O，便于单独测试。

    kind 为 move / read / all / quit / noop。相对移动只以上一次「指令值」为基准，
    绝不使用读回值——读回结果仅用于观察，不能自动触发纠正动作。
    """
    text = raw.strip()
    keyword = text.lower()
    if keyword in ("q", "quit", "exit"):
        return ("quit", None)
    if keyword == "r":
        return ("read", None)
    if keyword == "a":
        return ("all", None)
    if text[:1] in ("+", "-"):
        digits = text[1:].strip()
        if not digits:
            delta = step
        else:
            try:
                delta = int(digits)
            except ValueError:
                return ("noop", "相对步长必须是整数，例如 +5")
        if delta <= 0:
            return ("noop", "相对步长必须为正整数，方向由 +/- 决定")
        if last_cmd is None:
            return ("noop", "还没有绝对指令作为基准，请先输入一个绝对角度")
        return ("move", last_cmd + (delta if text[0] == "+" else -delta))
    try:
        return ("move", int(text))
    except ValueError:
        return ("noop", "无法识别；输入 + / - / 绝对角度 / r 重读 / a 读全部 / q 退出")


def run_jog(controller, joint, low, high, step, log_path,
            ask=input, max_moves=DEFAULT_MAX_MOVES):
    """单关节步进探测。每次移动都由操作者发起，以便目视判断机械限位。"""
    event = dict(session_id=str(uuid.uuid4()),
                 mode="real" if controller.real else "simulation",
                 joint=joint, envelope=[low, high], step=step,
                 motion_ms=controller.config.get("motion_ms"),
                 settle_ms=controller.config.get("settle_ms"),
                 max_moves=max_moves, status="running", started_at=now(), ended_at=None,
                 reason=None, moves_attempted=0, last_commanded=None, current_read=None)
    append_event(log_path, event)

    def record(kind, **fields):
        append_event(log_path, dict(session_id=event["session_id"], at=now(),
                                    kind=kind, joint=joint, **fields))

    print(f"探测关节 {joint}：包络 {low}~{high}，步长 {step}°，"
          f"单步 {event['motion_ms']}+{event['settle_ms']} ms，最多 {max_moves} 步")
    if low == high:
        print(f"提示：包络宽度为 0，只能下发 {low}°，+ / - 会被拒绝（保持测试）")
    elif step > high - low:
        print(f"提示：步长 {step}° 大于包络宽度 {high - low}°，+ / - 会立即越界被拒")

    startup = read_joint(controller, joint)
    event["current_read"] = startup["angle"]
    record("startup_read", read=startup["angle"], read_ok=startup["read_ok"],
           error=startup["error"])
    if startup["read_ok"]:
        print(f"当前读数 {startup['angle']}°（仅观察，不作为基准）")
        if not low <= startup["angle"] <= high:
            print(f"警告：当前读数 {startup['angle']}° 在包络 {low}~{high} 之外。"
                  f"第一条绝对指令会让关节从 {startup['angle']}° 移动到目标值，"
                  f"移动量 = 目标 − {startup['angle']}。")
    else:
        print(f"当前读数无效（{startup['error']}）：仅观察，无法作为基准，请先输入绝对角度。")

    last_cmd = None
    attempted = 0
    try:
        try:
            answer = ask("输入 y 开始本次探测，其他输入中止（不会下发任何指令）：")
        except (EOFError, KeyboardInterrupt):
            print()
            event.update(status="aborted", reason="确认阶段被中断")
            print("已中止，未下发任何指令。")
            return event
        if answer.strip().lower() != "y":
            event.update(status="aborted", reason="用户未确认")
            print("已中止，未下发任何指令。")
            return event
        while True:
            if attempted >= max_moves:
                event["reason"] = f"已达本次上限 {max_moves} 步"
                print(f"已达本次上限 {max_moves} 步，停止。如需继续请重新运行。")
                break
            used = f"已用 {attempted}/{max_moves}"
            base = f"上一条指令 {last_cmd}°" if last_cmd is not None else "还没有绝对指令"
            try:
                raw = ask(f"[关节{joint} 包络{low}-{high} 步长{step}° {used} {base}] "
                          f"输入 +/- 或绝对角度，r 重读 a 读全部 q 退出：")
            except (EOFError, KeyboardInterrupt):
                print()
                event["reason"] = "已收到中断"
                print("已收到中断，停止下发后续指令。")
                break
            kind, payload = parse_jog_command(raw, last_cmd, step)
            if kind == "quit":
                event["reason"] = "用户退出"
                break
            if kind == "noop":
                print(f"  {payload}")
                continue
            if kind == "read":
                row = read_joint(controller, joint)
                record("read", read=row["angle"], read_ok=row["read_ok"], error=row["error"])
                print(f"  读数 {row['angle']}°（read_ok={row['read_ok']}）"
                      if row["read_ok"] else f"  读取失败：{row['error']}")
                continue
            if kind == "all":
                for row in controller.read_report():
                    record("read_all", target_id=row["id"], read=row["angle"],
                           read_ok=row["read_ok"], error=row["error"])
                    print(f"  {row['id']} 号：" + (f"{row['angle']}°" if row["read_ok"]
                                                  else f"读取失败 {row['error']}"))
                continue
            target = payload
            try:
                angle(controller.config, joint, target)
            except ValueError as exc:
                # 非法目标不消耗步数预算，也绝不截断或吸附到邻近合法值。
                record("rejected", commanded=target, reason=str(exc))
                print(f"  拒绝：{exc}")
                continue
            attempted += 1
            controller.write([(joint, target)])
            last_cmd = target
            row = read_joint(controller, joint)
            record("move", commanded=target, read=row["angle"],
                   read_ok=row["read_ok"], error=row["error"])
            if not row["read_ok"]:
                event["reason"] = f"回读失败：{row['error']}"
                print(f"  回读失败：{row['error']}；停止后续下发。")
                break
            offset = row["angle"] - target
            note = "" if offset == 0 else f"，偏差 {offset:+d}°（仅记录，不据此修正）"
            print(f"  回读 {row['angle']}°{note}")
    except BaseException as exc:
        event.update(status="aborted", reason=f"{type(exc).__name__}: {exc}")
        raise
    else:
        event["status"] = "completed"
    finally:
        event["ended_at"] = now()
        event["moves_attempted"] = attempted
        event["last_commanded"] = last_cmd
        append_event(log_path, event)
        print(f"探测结束：{attempted} 步，最后一条指令 {last_cmd}°；记录：{log_path}")
        print("未自动回零、未松爪。已发出的运动可能继续；Ctrl+C 不等同硬件急停。")
    return event


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--real", action="store_true", help="连接实际机械臂；默认仅模拟")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read", help="读取六个关节角度")
    sub.add_parser("targets", help="列出放置区域")
    save = sub.add_parser("capture", help="从实机读数记录一个姿态，不发送动作")
    save.add_argument("name")
    move = sub.add_parser("pose", help="执行已配置姿态")
    move.add_argument("name")
    grip = sub.add_parser("gripper")
    grip.add_argument("state", choices=["open", "closed"])
    joint = sub.add_parser("joint", help="单关节绝对角度运动")
    joint.add_argument("id", type=int, choices=range(1, 7))
    joint.add_argument("angle", type=int)
    run = sub.add_parser("run", help="一次固定点搬运")
    run.add_argument("--target", help="省略时终端选择")
    run.add_argument("--step", action="store_true", help="逐步输入 y 执行")
    jog = sub.add_parser("jog", help="单关节步进探测，用于实测关节范围")
    jog.add_argument("id", type=int, choices=range(1, 7))
    jog.add_argument("--envelope", type=int, nargs=2, metavar=("LOW", "HIGH"),
                     help="本次探测的角度包络；省略时取配置中该关节已核对的范围")
    jog.add_argument("--step", type=int, default=5, help="相对步长，默认 5")
    jog.add_argument("--max-moves", type=int, default=DEFAULT_MAX_MOVES,
                     help=f"单次最多移动步数，默认 {DEFAULT_MAX_MOVES}")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.command == "targets":
        print("可选区域：" + ", ".join(config.get("targets", {})))
        return
    if args.command == "capture" and not args.real:
        raise ValueError("capture 仅允许 --real，防止把模拟读数当成实测姿态")
    if args.command == "run":
        args.target = args.target or input("选择区域（" + ", ".join(config["targets"]) + "）：").strip()
        plan(config, args.target)
    if args.command == "jog":
        if not args.real:
            raise ValueError("jog 仅允许 --real，防止把模拟动作当成实测范围")
        if type(args.step) is not int or isinstance(args.step, bool) or not 1 <= args.step <= 180:
            raise ValueError("步长必须是 1～180 的整数")
        if type(args.max_moves) is not int or args.max_moves < 1:
            raise ValueError("步数上限必须是正整数")
        configured = config.get("joint_limits", {}).get(str(args.id))
        checked = (isinstance(configured, list) and len(configured) == 2
                   and all(number(v) for v in configured))
        if args.envelope is None:
            if not checked:
                raise ValueError(f"未提供 --envelope，且配置中关节 {args.id} 尚未填写已核对范围")
            args.envelope = [int(v) for v in configured]
        elif checked and not (configured[0] <= args.envelope[0]
                              <= args.envelope[1] <= configured[1]):
            print(f"警告：包络 {args.envelope} 超出配置中关节 {args.id} 的已核对范围 "
                  f"{configured}；本次为临时探测，不会写盘。")
        args.probe = probe_config(config, args.id, args.envelope[0], args.envelope[1])
        # 借用统一校验入口验证包络本身，不另写一套会分叉的边界比较。
        angle(args.probe, args.id, args.envelope[0])
        angle(args.probe, args.id, args.envelope[1])
    movement = args.command in ("joint", "pose", "gripper", "run", "jog")
    if movement:
        timing(config)
        if args.command == "pose":
            get_pose(config, args.name)
        elif args.command == "gripper":
            angle(config, 6, config["gripper"][args.state])
        elif args.command == "joint":
            angle(config, args.id, args.angle)
        if args.real and config.get("hardware_ready") is not True:
            raise ValueError("核对关节范围、停止方式和当前空载姿态后，将 hardware_ready 设为 true")

    def dispatch():
        controller = Controller(args.probe if args.command == "jog" else config, args.real)
        if args.command == "read":
            results = controller.read_report()
            report = dict(recorded_at=now(), mode="real" if args.real else "simulation", joints=results)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            append_event(ROOT / "logs" / ("read_real.jsonl" if args.real else "read_simulation.jsonl"), report)
            if not all(row["read_ok"] for row in results):
                raise RuntimeError("部分舵机读取失败，详情见上方输出；未重试、未发送运动指令")
        elif args.command == "capture":
            capture(config, args.config, args.name, controller.read())
        elif args.command == "joint":
            controller.write([(args.id, args.angle)])
        elif args.command in ("pose", "gripper"):
            controller.execute(args.command, args.name if args.command == "pose" else args.state)
        elif args.command == "run":
            run_task(controller, args.target, args.step,
                     ROOT / "logs" / ("real.jsonl" if args.real else "simulation.jsonl"))
        elif args.command == "jog":
            run_jog(controller, args.id, args.envelope[0], args.envelope[1], args.step,
                    ROOT / "logs" / f"jog_{args.id}.jsonl", max_moves=args.max_moves)

    if args.real:
        with hardware_lock():
            dispatch()
    else:
        dispatch()


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"已中止：{type(error).__name__}: {error}")
        print("不再下发后续指令；已发出的动作可能继续。Ctrl+C 不等同硬件急停。")
        raise SystemExit(1)
