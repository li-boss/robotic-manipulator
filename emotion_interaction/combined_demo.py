"""One camera and one window for smile + hand gestures. Local/mock only."""
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
    parser.add_argument("--mock-robot-state", choices=["IDLE","BUSY","HOLDING","FAULT","STOPPING","STOPPED"])
    args = parser.parse_args()
    print("正在启动笑脸与手势联合检测……", flush=True)
    import cv2
    from emotion_interaction.combined_session import CombinedSession
    from emotion_interaction.gesture_detector import GestureDetector
    from emotion_interaction.smile_detector import SmileDetector
    from emotion_interaction.smile_policy import SmileConfig
    from emotion_interaction.preview_overlay import draw_combined_overlay
    from integration_tools.mocks.fake_robot import FakeRobot
    config = SmileConfig(threshold=args.smile_threshold, requested_action=args.action)
    robot = FakeRobot(args.mock_robot_state) if args.mock_robot_state else None
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
                record["mock_decision"] = robot.interact(request, time.time_ns()//1_000_000) if robot and request else None
                log.write(json.dumps(record) + "\n")
                if request or record["gesture_candidate"]:
                    log.flush()
                    print(json.dumps({"smile_request":request,"gesture_candidate":record["gesture_candidate"],
                                      "mock_decision":record["mock_decision"],"transport":"NONE"}), flush=True)
                draw_combined_overlay(frame,record,smile_threshold=args.smile_threshold)
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
