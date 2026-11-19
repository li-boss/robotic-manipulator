"""帧来源：电脑摄像头 / 开发板摄像头快照 / 无（键盘模拟时）。"""
import time


class WebcamSource:
    """电脑自带或 USB 摄像头（cv2.VideoCapture）。"""

    def __init__(self, index=0):
        import cv2
        self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            raise RuntimeError(f'打不开电脑摄像头 {index}')

    def read(self):
        ok, frame = self.capture.read()
        return frame if ok else None

    def close(self):
        self.capture.release()


class BoardSource:
    """开发板摄像头的最新一帧（GET /api/v1/frame.jpg），约 5～10 帧/秒。"""

    def __init__(self, client, min_interval_s=0.1):
        self.client, self.min_interval_s, self.last = client, min_interval_s, 0

    def read(self):
        import cv2
        import numpy as np
        wait = self.min_interval_s - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        jpeg = self.client.snapshot()
        return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)

    def close(self):
        pass


class NoSource:
    def read(self):
        time.sleep(0.05)
        return None

    def close(self):
        pass


def open_source(name, client):
    if name == 'none':
        return NoSource()
    if name == 'board':
        return BoardSource(client)
    if name.startswith('webcam'):
        return WebcamSource(int(name[6:] or 0))
    raise ValueError('--source 取值：webcam / webcam1 / board / none')


def load_class(spec, **kwargs):
    """'package.module:ClassName' → 实例。用来插拔队友的检测器/识别器。"""
    import importlib
    module, _, name = spec.partition(':')
    return getattr(importlib.import_module(module), name)(**kwargs)
