"""Camera/video local preview. Never sends requests to a real robot."""
import argparse
import json
import time
import uuid
from pathlib import Path


def main():
    print("正在启动手势识别……", flush=True)
    import cv2
    from emotion_interaction.gesture_detector import GestureDetector
    from emotion_interaction.gesture_policy import GesturePolicy
    from emotion_interaction.preview_overlay import draw_hand_boxes, hand_status
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()
    detector, policy = GestureDetector(progress=lambda message: print(message, flush=True)), GesturePolicy()
    print("正在打开视频文件……" if args.video else f"正在打开摄像头 {args.camera}……", flush=True)
    cap = cv2.VideoCapture(str(args.video) if args.video else args.camera)
    session = uuid.uuid4().hex
    output = Path("logs") / f"gesture-{session}.jsonl"
    output.parent.mkdir(exist_ok=True)
    try:
        if not cap.isOpened():
            raise RuntimeError("CAMERA_UNAVAILABLE: check camera index or video path")
        previous = 0
        index = 0
        with output.open("w", encoding="utf-8") as log:
            while True:
                ok, frame = cap.read()
                if not ok:
                    print("VIDEO_END" if args.video else "CAMERA_UNAVAILABLE")
                    break
                captured_at = max(time.time_ns() // 1_000_000, previous + 1)
                previous = captured_at
                frame_id = f"{session}-{index}"
                index += 1
                prediction = detector.detect(frame)
                now = time.time_ns() // 1_000_000
                candidate = policy.update(frame_id=frame_id, captured_at=captured_at,
                    now_ms=now, gesture=prediction["gesture"], confidence=prediction["confidence"])
                draw_hand_boxes(frame,prediction)
                text = f"{prediction['raw_label']} {prediction['confidence']:.2f} {hand_status(prediction)}"
                cv2.putText(frame, text, (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
                cv2.putText(frame, "LOCAL ONLY - gesture contract pending", (20,75), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,200,255), 1)
                log.write(json.dumps({"frame_id": frame_id, "captured_at": captured_at,
                    **prediction, "candidate": candidate}) + "\n")
                if candidate:
                    print(json.dumps(candidate))
                if not args.headless:
                    cv2.imshow("Gesture preview - Q to quit", frame)
                if (args.max_frames > 0 and index >= args.max_frames) or (not args.headless and cv2.waitKey(1) & 0xFF in (ord("q"), 27)):
                    break
    finally:
        cap.release()
        detector.close()
        if not args.headless:
            cv2.destroyAllWindows()
        print(f"Log: {output.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已取消手势识别。", flush=True)
