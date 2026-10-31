"""Smile + gesture preview; smile can directly execute a confirmed JJT plan."""
import argparse
import json
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from emotion_interaction.smile_policy import DEFAULT_SMILE_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description="Combined smile and gesture preview")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--smile-threshold", type=float, default=DEFAULT_SMILE_THRESHOLD)
    parser.add_argument("--action", choices=["WAVE", "NOD"], default="WAVE")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--robot-url", help="JJT origin, for example http://192.168.137.100:8080")
    parser.add_argument("--states", help="ordered JJT saved-state names separated by commas")
    args = parser.parse_args()
    print("正在启动笑脸与手势联合检测……", flush=True)
    import cv2
    from emotion_interaction.combined_session import CombinedSession
    from emotion_interaction.gesture_detector import GestureDetector
    from emotion_interaction.smile_detector import SmileDetector
    from emotion_interaction.smile_policy import SmileConfig
    from emotion_interaction.preview_overlay import draw_combined_overlay
    from emotion_interaction.interaction_request import (
        JjtError, JjtRobot, confirm_and_execute, parse_state_names,
    )
    config = SmileConfig(threshold=args.smile_threshold, requested_action=args.action)
    if bool(args.robot_url) != bool(args.states):
        parser.error("--robot-url and --states must be provided together")
    robot = JjtRobot(args.robot_url) if args.robot_url else None
    state_names = parse_state_names(args.states) if args.states else None
    if robot:
        status = robot.status()
        print(f"JJT已连接：{robot.origin}，模式={status['mode']}，忙碌={status['busy']}", flush=True)
    identity = uuid.uuid4().hex
    path = Path("logs") / f"combined-{identity}.jsonl"
    path.parent.mkdir(exist_ok=True)
    try:
        with ExitStack() as resources:
            progress = lambda message: print(message, flush=True)
            smile = SmileDetector(progress=progress)
            resources.callback(smile.close)
            gesture = GestureDetector(progress=progress)
            resources.callback(gesture.close)
            session = CombinedSession(smile, gesture, smile_config=config)
            cap = cv2.VideoCapture(str(args.video) if args.video else args.camera)
            resources.callback(cap.release)
            if not args.headless:
                resources.callback(cv2.destroyAllWindows)
            if not cap.isOpened():
                raise RuntimeError("CAMERA_UNAVAILABLE: check camera index or video path")
            print("已打开一个视频源，同时检测笑脸和手势。按 Q 或 Esc 退出。", flush=True)
            log = resources.enter_context(path.open("w", encoding="utf-8"))
            index = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    if args.video:
                        print("VIDEO_END", flush=True)
                        break
                    raise RuntimeError("CAMERA_UNAVAILABLE: capture disconnected")
                captured_at = time.time_ns() // 1_000_000
                start = time.perf_counter()
                record = session.process(frame, frame_id=f"{identity}-{index}", captured_at=captured_at)
                index += 1
                elapsed_ms = (time.perf_counter()-start)*1000
                record["processing_ms"] = elapsed_ms
                request = record["smile_request"]
                if request and robot:
                    try:
                        result = confirm_and_execute(robot, request, state_names)
                    except JjtError as error:
                        result = {"decision": "FAILED", "reason": str(error),
                                  "request_id": request["request_id"]}
                elif request:
                    result = {"decision": "LOCAL_ONLY", "reason": "ROBOT_URL_NOT_CONFIGURED",
                              "request_id": request["request_id"]}
                else:
                    result = None
                record["robot_result"] = result
                record["transport"] = "JJT_DIRECT" if robot else "LOCAL_ONLY"
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                if request or record["gesture_candidate"]:
                    log.flush()
                    print(json.dumps({"smile_request":request,"gesture_candidate":record["gesture_candidate"],
                                      "robot_result":result,"transport":record["transport"]},
                                      ensure_ascii=False), flush=True)
                draw_combined_overlay(frame, record, smile_threshold=args.smile_threshold,
                                      robot_connected=robot is not None)
                if not args.headless:
                    cv2.imshow("Smile + Gesture preview", frame)
                if (args.max_frames > 0 and index >= args.max_frames) or (not args.headless and cv2.waitKey(1) & 0xFF in (ord("q"),27)):
                    break
    finally:
        print(f"Log: {path.resolve()}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已退出联合检测。", flush=True)
