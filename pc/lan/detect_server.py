"""局域网方块识别服务（jjt-detect/1 参考实现）：方块识别同学在自己电脑上运行。

把任意 BlockDetector 子类挂成 HTTP 服务：收一张 JPEG 原图，返回图中方块的像素位置。
只做识别，不连接开发板、不触发运动、没有任何“放行/跳过检查”选项。协议见 docs/方块识别局域网接口.md。

    python pc/lan/detect_server.py --detector block_vision.yolo_detector:YoloBlockDetector --host 0.0.0.0 --port 8100
    python pc/lan/detect_server.py --detector block_vision.yolo_detector:YoloBlockDetector --selftest 图片.jpg
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lan.detect_protocol import PROTOCOL, ProtocolError, check_frame_id, parse_reply  # noqa: E402
from armlink.frames import load_class  # noqa: E402

MAX_JPEG_BYTES = 5 * 1024 * 1024


class DetectService:
    def __init__(self, detector, model_name):
        self.detector, self.model_name = detector, model_name
        self.lock = threading.Lock()   # 模型一般不保证线程安全：一次只识别一张

    def detect(self, jpeg, frame_id, size):
        import cv2
        import numpy as np
        check_frame_id(frame_id)
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ProtocolError('收到的不是可解码的 JPEG')
        if (image.shape[1], image.shape[0]) != tuple(size):
            raise ProtocolError(f'图像实际尺寸 {image.shape[1]}×{image.shape[0]} 与请求头 {size[0]}×{size[1]} 不符')
        started = time.perf_counter()
        with self.lock:
            found = self.detector.detect(image)
        reply = dict(protocol=PROTOCOL, frame_id=frame_id, model=self.model_name,
                     ms=round((time.perf_counter() - started) * 1000, 1),
                     detections=[d.to_dict() for d in found][:20])
        parse_reply(reply, frame_id, size)   # 发出前用同一份协议自检，不合规就报错而不是发出去
        return reply


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % args))

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, dict(protocol=PROTOCOL, ready=True, model=service.model_name))
            self.reply(404, dict(error='无此接口'))

        def do_POST(self):
            if self.path != '/detect':
                return self.reply(404, dict(error='无此接口'))
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'image/jpeg':
                    raise ProtocolError('Content-Type 须为 image/jpeg')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_JPEG_BYTES:
                    raise ProtocolError('图像大小须为 1 字节～5 MB')
                width, height = int(self.headers.get('X-Image-Width', '0')), int(self.headers.get('X-Image-Height', '0'))
                if not (0 < width <= 4096 and 0 < height <= 4096):
                    raise ProtocolError('X-Image-Width / X-Image-Height 无效')
                result = service.detect(self.rfile.read(size), self.headers.get('X-Frame-Id'), (width, height))
            except (ProtocolError, ValueError) as exc:
                return self.reply(400, dict(error=str(exc)))
            except Exception as exc:  # noqa: BLE001  模型内部异常
                return self.reply(500, dict(error=f'{type(exc).__name__}: {exc}'))
            self.reply(200, result)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--detector', default='block_vision.color_detector:ColorBlockDetector', help='检测器类 模块:类名')
    parser.add_argument('--model-name', help='写进回复的模型名/版本（默认用类名）')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8100)
    parser.add_argument('--selftest', metavar='JPG', help='只用一张本地图片自检并打印回复，不启动服务')
    args = parser.parse_args()
    service = DetectService(load_class(args.detector), args.model_name or args.detector)
    if args.selftest:
        import cv2
        jpeg = Path(args.selftest).read_bytes()
        image = cv2.imread(args.selftest)
        if image is None:
            raise SystemExit('读不了图片：' + args.selftest)
        print(json.dumps(service.detect(jpeg, '0' * 32, (image.shape[1], image.shape[0])), ensure_ascii=False, indent=2))
        return
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service))
    print(f'方块识别服务 {PROTOCOL}：http://{args.host}:{args.port}  检测器 {args.detector}（只识别，不连开发板）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
