"""Smile preview with optional direct execution through the JJT web API."""
import argparse
import json
import time
import uuid
from pathlib import Path
from emotion_interaction.smile_policy import DEFAULT_SMILE_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description="Smile preview and direct JJT saved-state execution")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SMILE_THRESHOLD)
    parser.add_argument("--action", choices=["WAVE", "NOD"], default="WAVE")
    parser.add_argument("--robot-url", help="JJT origin, for example http://192.168.137.100:8080")
    parser.add_argument("--states", help="ordered JJT saved-state names separated by commas")
    args = parser.parse_args()
    print("正在启动笑脸检测……", flush=True)
    import cv2
    from emotion_interaction.smile_detector import SmileDetector
    from emotion_interaction.smile_policy import SmilePolicy, SmileConfig
    from emotion_interaction.interaction_request import (
        JjtError, JjtRobot, confirm_and_execute, parse_state_names,
    )
    policy = SmilePolicy(SmileConfig(threshold=args.threshold, requested_action=args.action))
    if bool(args.robot_url) != bool(args.states):
        parser.error("--robot-url and --states must be provided together")
    robot = JjtRobot(args.robot_url) if args.robot_url else None
    state_names = parse_state_names(args.states) if args.states else None
    if robot:
        status = robot.status()
        print(f"JJT已连接：{robot.origin}，模式={status['mode']}，忙碌={status['busy']}", flush=True)
    detector = SmileDetector(progress=lambda message: print(message, flush=True))
    cap = None
    session = uuid.uuid4().hex
    output = Path("logs") / f"smile-{session}.jsonl"
    output.parent.mkdir(exist_ok=True)
    try:
        print("正在打开视频……" if args.video else f"正在打开摄像头 {args.camera}……", flush=True)
        cap = cv2.VideoCapture(str(args.video) if args.video else args.camera)
        if not cap.isOpened():
            raise RuntimeError("CAMERA_UNAVAILABLE: check camera index or video path")
        index = 0
        with output.open("w", encoding="utf-8") as log:
            while True:
                ok, frame = cap.read()
                if not ok:
                    if args.video:
                        print("VIDEO_END", flush=True)
                        break
                    raise RuntimeError("CAMERA_UNAVAILABLE: capture disconnected")
                captured_at = time.time_ns() // 1_000_000
                frame_id = f"{session}-{index}"
                index += 1
                evidence = detector.detect(frame)
                now_ms = time.time_ns() // 1_000_000
                request = policy.update(frame_id=frame_id, captured_at=captured_at,
                                        now_ms=now_ms, evidence=evidence)
                if request and robot:
                    try:
                        decision = confirm_and_execute(robot, request, state_names)
                    except JjtError as error:
                        decision = {"decision": "FAILED", "reason": str(error),
                                    "request_id": request["request_id"]}
                elif request:
                    decision = {"decision": "LOCAL_ONLY", "reason": "ROBOT_URL_NOT_CONFIGURED",
                                "request_id": request["request_id"]}
                else:
                    decision = None
                record = {"frame_id": frame_id, "captured_at": captured_at, "evidence": evidence,
                          "request": request, "robot_result": decision,
                          "transport": "JJT_DIRECT" if robot else "LOCAL_ONLY"}
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                if request:
                    log.flush()
                    print(json.dumps({"status": "SMILE_TRIGGERED", "request": request,
                                      "robot_result": decision}, ensure_ascii=False), flush=True)
                score = evidence["smile_score"]
                smiling = evidence["status"] == "FACE_DETECTED" and score >= args.threshold
                title = ("SMILE" if smiling else "NO_SMILE") if evidence["status"] == "FACE_DETECTED" else evidence["status"]
                cv2.putText(frame, f"{title} score={score:.2f} faces={evidence['face_count']}",
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX, .7, (0,255,0), 2)
                mode = "JJT DIRECT" if robot else "LOCAL ONLY"
                cv2.putText(frame, f"{mode} - Q to quit", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, .6, (0,200,255), 1)
                box = evidence["face_box"]
                if box:
                    h, w = frame.shape[:2]
                    cv2.rectangle(frame, (int(box[0]*w),int(box[1]*h)),
                                  (int(box[2]*w),int(box[3]*h)), (0,255,0), 1)
                if not args.headless:
                    cv2.imshow("Smile preview", frame)
                if (args.max_frames > 0 and index >= args.max_frames) or (not args.headless and cv2.waitKey(1) & 0xFF in (ord("q"),27)):
                    break
    finally:
        if cap is not None:
            cap.release()
        detector.close()
        if not args.headless:
            cv2.destroyAllWindows()
        print(f"Log: {output.resolve()}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已取消笑脸检测。", flush=True)
