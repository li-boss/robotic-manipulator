"""只读识别预览：支持电脑摄像头或开发板摄像头，不发送机械臂动作请求。"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armlink import BoardClient, DEFAULT_BOARD
from frames import open_source
from gesture.recognizer import CombinedRecognizer


def main():
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['combined', 'gesture', 'smile'], default='combined')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--source', choices=['webcam', 'board'], default='webcam',
                        help='webcam（电脑摄像头，默认）/ board（开发板摄像头，只读）')
    parser.add_argument('--board', default=DEFAULT_BOARD, help='开发板地址')
    parser.add_argument('--smile-threshold', type=float, default=0.2,
                        help='微笑系数下限（默认0.2）')
    args = parser.parse_args()
    recognizer = CombinedRecognizer(mode=args.mode, smile_threshold=args.smile_threshold)
    client = BoardClient(args.board)
    source_name = 'board' if args.source == 'board' else ('webcam' if args.camera == 0 else f'webcam{args.camera}')
    source = None
    try:
        source = open_source(source_name, client)
        print(f'Camera source: {args.source} | read only; no robot commands', flush=True)
        previous = object()
        while True:
            image = source.read()
            if image is None:
                raise RuntimeError('Camera frame read failed')
            started = time.monotonic()
            event = recognizer.recognize(image)
            last = recognizer.last
            hand, face = last.get('hand', {}), last.get('face', {})
            label = event.label if event else None
            if label != previous:
                print(f'Recognition: {event}', flush=True)
                previous = label
            lines = [
                f'Event: {event.label if event else "NONE"}',
                f'Hand: {hand.get("gesture") or hand.get("raw_label", "disabled")} {hand.get("confidence", 0):.2f} / 0.80',
                f'Smile: {face.get("smile_score", 0):.2f} / {args.smile_threshold:.2f} faces={face.get("face_count", 0)}',
                f'{(time.monotonic() - started) * 1000:.0f} ms | READ ONLY | Q: quit',
            ]
            for index, line in enumerate(lines):
                cv2.putText(image, line, (15, 30 + index * 28), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 230, 0), 2)
            cv2.imshow('D smile + gesture (no robot)', image)
            if cv2.waitKey(1) & 0xff in (ord('q'), ord('Q')):
                break
    finally:
        if source is not None:
            source.close()
        recognizer.close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
