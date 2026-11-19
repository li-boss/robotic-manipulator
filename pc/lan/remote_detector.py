"""Mac 端：把开发板冻结的原图经局域网发给方块识别同学的检测服务，取回像素结果（jjt-detect/1）。

只做“图 → 像素检测”的转发与校验，不连接开发板、不触发运动；提交给开发板仍由
run_block_grasp.py 经 armlink 完成，开发板照常定位、规划、碰撞检查。
协议见 docs/方块识别局域网接口.md。
"""
import json
import time
import urllib.error
import urllib.request

from block_vision.base import BlockDetector
from lan.detect_protocol import PROTOCOL, ProtocolError, check_frame_id, parse_reply


class RemoteDetectError(RuntimeError):
    """识别服务不可用、超时、出错或回复不合协议。本次不提交，由调用方停止。"""


class RemoteDetector(BlockDetector):
    def __init__(self, url, timeout_s=10.0, log=None):
        self.url = url.rstrip('/')
        self.timeout_s = timeout_s
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
            raise RemoteDetectError(f'识别服务返回 HTTP {exc.code}：{message}') from None
        except (urllib.error.URLError, OSError) as exc:
            raise RemoteDetectError(f'连不上识别服务 {self.url}：{getattr(exc, "reason", exc)}') from None
        except ValueError:
            raise RemoteDetectError('识别服务回复不是 JSON') from None

    def health(self):
        info = self._open(urllib.request.Request(self.url + '/health'), 5)
        if not isinstance(info, dict) or info.get('protocol') != PROTOCOL:
            raise RemoteDetectError(f'识别服务协议版本不符：{info!r}（需要 {PROTOCOL}）')
        if info.get('ready') is not True:
            raise RemoteDetectError('识别服务尚未就绪（模型加载中？）')
        self.model = info.get('model')
        return info

    def detect_jpeg(self, jpeg, frame_id, image_size):
        """转发开发板冻结的 JPEG 原图（不重新压缩），返回校验过的 [BlockDetection]。"""
        check_frame_id(frame_id)
        width, height = (int(v) for v in image_size)
        request = urllib.request.Request(self.url + '/detect', data=bytes(jpeg), method='POST', headers={
            'Content-Type': 'image/jpeg', 'X-Frame-Id': frame_id,
            'X-Image-Width': str(width), 'X-Image-Height': str(height)})
        started = time.monotonic()
        reply = self._open(request, self.timeout_s)
        self.last_ms = round((time.monotonic() - started) * 1000, 1)
        try:
            detections = parse_reply(reply, frame_id, (width, height))
        except ProtocolError as exc:
            raise RemoteDetectError('识别结果不合协议，本次不提交：' + str(exc)) from None
        self.log(f'远端识别 {len(detections)} 个候选，往返 {self.last_ms} ms'
                 + (f'（模型 {reply["model"]}，推理 {reply["ms"]} ms）' if reply.get('ms') is not None else ''))
        return detections

    def detect(self, image):
        """只有解码后的图像时（不经开发板冻结）：重新编码为 JPEG 再发送，用随机 frame_id。"""
        import secrets
        import cv2
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise RemoteDetectError('图像编码失败')
        return self.detect_jpeg(encoded.tobytes(), secrets.token_hex(16), (image.shape[1], image.shape[0]))
