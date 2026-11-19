"""Mac 端：把每一帧经局域网发给表情手势同学的识别服务，取回“这一帧是什么”（jjt-gesture/1）。

只做“图 → 表情/手势标签”的转发与校验，不连接开发板、不触发运动；连续多帧确认、
标签到舞蹈的映射、提交给开发板仍由 run_gesture_dance.py 完成。协议见 docs/表情手势局域网接口.md。
"""
import json
import secrets
import time
import urllib.error
import urllib.request

from detect_protocol import ProtocolError
from gesture.base import GestureRecognizer
from gesture_protocol import PROTOCOL, parse_reply


class RemoteRecognizeError(RuntimeError):
    """识别服务不可用、超时或出错（网络问题）。本帧当作“没识别到”，不会触发动作。"""


class RemoteProtocolError(RuntimeError):
    """识别服务回复不合协议。程序应停止，修好服务后再启动。"""


class RemoteRecognizer(GestureRecognizer):
    def __init__(self, url, timeout_s=3.0, log=None, jpeg_quality=85):
        self.url = url.rstrip('/')
        self.timeout_s, self.jpeg_quality = timeout_s, jpeg_quality
        self.log = log or (lambda text: None)
        # 局域网直连：不走系统代理（Mac 上有 127.0.0.1:7890 代理）。
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.model = None
        self.last_ms = None

    def _open(self, request, timeout):
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get('error', str(exc))
            except ValueError:
                message = str(exc)
            raise RemoteRecognizeError(f'识别服务返回 HTTP {exc.code}：{message}') from None
        except (urllib.error.URLError, OSError) as exc:
            raise RemoteRecognizeError(f'连不上识别服务 {self.url}：{getattr(exc, "reason", exc)}') from None
        except ValueError:
            raise RemoteProtocolError('识别服务回复不是 JSON') from None

    def health(self):
        info = self._open(urllib.request.Request(self.url + '/health'), 5)
        if not isinstance(info, dict) or info.get('protocol') != PROTOCOL:
            raise RemoteProtocolError(f'识别服务协议版本不符：{info!r}（需要 {PROTOCOL}）')
        if info.get('ready') is not True:
            raise RemoteRecognizeError('识别服务尚未就绪（模型加载中？）')
        self.model = info.get('model')
        return info

    def recognize_jpeg(self, jpeg, image_size):
        frame_id = secrets.token_hex(16)
        width, height = (int(v) for v in image_size)
        request = urllib.request.Request(self.url + '/recognize', data=bytes(jpeg), method='POST', headers={
            'Content-Type': 'image/jpeg', 'X-Frame-Id': frame_id,
            'X-Image-Width': str(width), 'X-Image-Height': str(height)})
        started = time.monotonic()
        reply = self._open(request, self.timeout_s)
        self.last_ms = round((time.monotonic() - started) * 1000, 1)
        try:
            return parse_reply(reply, frame_id)
        except ProtocolError as exc:
            raise RemoteProtocolError('识别结果不合协议：' + str(exc)) from None

    def recognize(self, image):
        if image is None:
            return None
        import cv2
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise RemoteRecognizeError('图像编码失败')
        return self.recognize_jpeg(encoded.tobytes(), (image.shape[1], image.shape[0]))
