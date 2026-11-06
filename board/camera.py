"""开发板 USB 摄像头采集与 MJPEG 推流。不导入 Arm_Lib、不涉及舵机。"""
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

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


def grab_frame(camera, max_age_ms=1000):
    """取最新一帧：返回 (BGR图像, JPEG字节, 帧序号, 采集时间毫秒)。过期或未连接时报错。"""
    import cv2
    import numpy as np
    with camera.condition:
        state = camera.status()
        if not state["connected"]:
            raise ValueError("摄像头未连接")
        jpeg = bytes(camera.jpeg)
        sequence = camera.sequence
        captured = int((time.time() - (time.monotonic() - camera.updated)) * 1000)
    if int(time.time() * 1000) - captured > max_age_ms:
        raise ValueError("摄像头画面已过期")
    frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("图像解码失败")
    return frame, jpeg, sequence, captured


def handler_for(camera):
    """基础处理器：/status 摄像头状态，/stream.mjpg 实时画面。server.py 在此基础上扩展。"""
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
                if path == "/status":
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
