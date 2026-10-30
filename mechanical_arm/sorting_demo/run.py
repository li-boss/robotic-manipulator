"""终端审阅一次颜色积木抓放。默认仅模拟；--real逐步确认，不自动重试。"""
import argparse
import json
from pathlib import Path
import urllib.request

from core import EventStore, arm, simulate, task_plan, validate_config

ROOT = Path(__file__).resolve().parent


def detection_from(url):
    with urllib.request.urlopen(url.rstrip("/") + "/detection", timeout=4) as response:
        return json.load(response)


def execute_real(detection, config, arm_config, store, refresh, ask=input, factory=arm.Controller):
    plan = task_plan(detection, config, arm_config)
    if arm_config.get("hardware_ready") is not True:
        raise ValueError("硬件未就绪")
    if plan["target"] not in config["verified_paths"]:
        raise ValueError("该目标尚未完成空载逐段路径验证；请先通过原控制程序验证")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print("必须核对空载起始姿态、固定取物标记、唯一控制来源和实际停止方式。")
    print("仅执行上述有限步骤；每步还需确认。中止只停止后续下发，不是硬件急停。")
    if ask("已审阅上述全部参数并批准本次任务？输入 y 继续：").strip().lower() != "y":
        return None
    # 人工审阅期间帧可能过期，重新观察。类别改变则整个任务拒绝，不自动改目标。
    current = refresh()
    current_plan = task_plan(current, config, arm_config)
    if current_plan != plan or current["color"] != detection["color"]:
        raise ValueError("检测结果变化，任务取消，请重新审阅")
    task_id = "real:" + current["request_id"]
    with arm.hardware_lock():
        events, _ = store.latest()
        if any(row["task_id"] == task_id for row in events):
            raise ValueError("重复任务，禁止重复抓取")
        event = dict(task_id=task_id, mode="real", color=current["color"], target=plan["target"],
                     started_at=arm.now(), ended_at=None, status="running", result=None,
                     retry_count=0, failure_reason=None, plan=plan, last_step=None)
        store.append(event)  # 写失败不初始化硬件、不运动。
        try:
            controller = factory(arm_config, real=True)
            for step in plan["steps"]:
                print(f"{step['label']}：{step['joints']}，{plan['motion_ms']} ms + 稳定 {plan['settle_ms']} ms")
                if ask("批准这一具体步骤？输入 y 下发，其他输入中止：").strip().lower() != "y":
                    raise InterruptedError("用户中止")
                event["last_step"] = step["label"]
                store.append(event)  # 每步写入失败停止，不在日志故障后继续运动。
                controller.execute(step["kind"], step["name"])
            event["status"] = "awaiting_confirmation"
            store.append(event)
            answer = ask("实际正确放置了吗？y=成功，n=失败，其他=未确认：").strip().lower()
            event["result"] = {"y": "success", "n": "failed"}.get(answer, "unconfirmed")
            if answer == "n":
                event["failure_reason"] = ask("失败原因：").strip() or "人工确认失败"
            event["status"] = "completed"
        except BaseException as exc:
            event.update(status="aborted", result="failed", failure_reason=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            event["ended_at"] = arm.now()
            store.append(event)
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://192.168.137.100:8080")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8-sig")))
    arm_config = json.loads((ROOT.parent / "arm_playground/config.json").read_text(encoding="utf-8-sig"))
    store = EventStore(ROOT / "logs/events.jsonl")
    detection = detection_from(args.url)
    if args.real:
        event = execute_real(detection, config, arm_config, store, lambda: detection_from(args.url))
    else:
        if "request_id" not in detection:
            raise ValueError(detection.get("reason", "摄像头未就绪"))
        event = simulate(detection, config, arm_config, store)
    print(json.dumps(event, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"已停止：{exc}；无自动重试、回零或松爪。Ctrl+C不等同硬件急停。")
        raise SystemExit(1)
