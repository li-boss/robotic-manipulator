"""示例识别器：不用模型，按键模拟手势，方便在没有模型时联调机械臂跳舞。

运行 run_gesture_dance.py --recognizer gesture.keyboard:KeyboardRecognizer --source none，
在终端输入标签名（如 smile）回车即视为识别到一次。
"""
import queue
import sys
import threading

from armlink import GestureEvent
from .base import GestureRecognizer


class KeyboardRecognizer(GestureRecognizer):
    def __init__(self):
        self.events = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        print('键盘模拟：输入手势标签后回车（例如 smile / wave），Ctrl+C 退出', flush=True)

    def _read(self):
        for line in sys.stdin:
            if line.strip():
                self.events.put(line.strip())

    def recognize(self, image):
        try:
            return GestureEvent(label=self.events.get_nowait(), confidence=1.0)
        except queue.Empty:
            return None
