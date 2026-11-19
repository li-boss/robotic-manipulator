"""独立的 YOLO 推理服务：接收冻结帧 JPEG，只返回像素检测结果，不连接开发板。"""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from block_vision.yolo_detector import YoloBlockDetector  # noqa: E402

MAX_JPEG_BYTES = 8 * 1024 * 1024


def handler_for(detector):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/infer':
                self._reply(404, {'error': '无此接口'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Content-Type') != 'image/jpeg' or not 0 < size <= MAX_JPEG_BYTES:
                    raise ValueError('须提交不超过 8 MB 的 JPEG 冻结画面')
                jpeg = self.rfile.read(size)
                if len(jpeg) != size:
                    raise ValueError('JPEG 数据不完整')
                image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError('JPEG 无法解码')
                candidates = detector.detect(image)
                self._reply(200, {'image_sha256': hashlib.sha256(jpeg).hexdigest(),
                                  'detections': [candidate.to_dict() for candidate in candidates]})
            except ValueError as exc:
                self._reply(400, {'error': str(exc)})
            except Exception as exc:
                self._reply(503, {'error': f'视觉推理失败：{exc}'})

        def _reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8096)
    args = parser.parse_args()
    detector = YoloBlockDetector()
    print(f'YOLO 推理服务 http://{args.host}:{args.port}/infer（只做识别）', flush=True)
    ThreadingHTTPServer((args.host, args.port), handler_for(detector)).serve_forever()


if __name__ == '__main__':
    main()
