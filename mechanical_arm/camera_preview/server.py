"""Camera-only HTTP preview. No Arm_Lib or motor interfaces."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


class Camera:
    def __init__(self, device):
        self.device = device
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.jpeg = None
        self.sequence = 0
        self.updated = 0
        self.error = "正在连接摄像头"
        self.size = None

    def unavailable(self, message):
        with self.condition:
            self.jpeg = None
            self.error = message
            self.condition.notify_all()

    def status(self):
        with self.condition:
            fresh = self.jpeg is not None and time.monotonic() - self.updated < 3
            return dict(connected=fresh, device=self.device, resolution=self.size,
                        message="画面已连接" if fresh else self.error or "摄像头画面超时")

    def capture(self):
        import cv2
        while not self.stop.is_set():
            if not Path(self.device).exists():
                self.unavailable(f"未检测到 {self.device}，请将 USB 摄像头接入开发板")
                self.stop.wait(2)
                continue
            cap = None
            try:
                cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                if not cap.isOpened():
                    raise RuntimeError("摄像头打开失败，请检查是否被其他程序占用")
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                while not self.stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("读取画面失败，正在重新连接")
                    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if not ok:
                        raise RuntimeError("图像编码失败")
                    with self.condition:
                        self.jpeg = encoded.tobytes()
                        self.sequence += 1
                        self.updated = time.monotonic()
                        self.size = [frame.shape[1], frame.shape[0]]
                        self.error = None
                        self.condition.notify_all()
                    self.stop.wait(0.1)
            except Exception as exc:
                self.unavailable(str(exc))
            finally:
                if cap is not None:
                    cap.release()
            self.stop.wait(2)


def handler_for(camera):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, mime, body):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    self.reply(200, "text/html; charset=utf-8", (ROOT / "index.html").read_bytes())
                elif path == "/status":
                    self.reply(200, "application/json; charset=utf-8",
                               json.dumps(camera.status(), ensure_ascii=False).encode())
                elif path == "/stream.mjpg":
                    if not camera.status()["connected"]:
                        self.reply(503, "text/plain; charset=utf-8", "摄像头未就绪".encode())
                        return
                    self.connection.settimeout(5)
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    sequence = -1
                    while not camera.stop.is_set():
                        with camera.condition:
                            camera.condition.wait_for(
                                lambda: camera.sequence != sequence or camera.jpeg is None or camera.stop.is_set(),
                                timeout=3)
                            if camera.stop.is_set() or not camera.status()["connected"]:
                                break
                            if camera.sequence == sequence:
                                break
                            data, sequence = camera.jpeg, camera.sequence
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                        self.wfile.flush()
                    self.close_connection = True
                else:
                    self.reply(404, "text/plain", b"Not found")
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def log_message(self, format, *args):
            if self.path != "/status":
                super().log_message(format, *args)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.137.100")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--device", default="/dev/video0")
    args = parser.parse_args()
    camera = Camera(args.device)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(camera))
    threading.Thread(target=camera.capture, daemon=True).start()
    print(f"Camera preview: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop.set()
        with camera.condition:
            camera.condition.notify_all()
        server.server_close()


if __name__ == "__main__":
    main()
