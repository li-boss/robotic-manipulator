"""开发板 HTTP 接口的 Python 客户端（只用标准库）。接口说明见 docs/接口与合并指南.md。"""
import base64
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .types import BlockDetection, Frame

DEFAULT_BOARD = 'http://192.168.137.100:8080'


class BoardError(RuntimeError):
    """开发板拒绝了请求（status 为 HTTP 状态码，message 为开发板给出的中文原因）。"""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class BoardClient:
    def __init__(self, base_url=DEFAULT_BOARD, timeout=10):
        self.base = base_url.rstrip('/')
        self.timeout = timeout

    # ------------------------------------------------------------ 底层
    def _request(self, path, body=None, timeout=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, method='GET' if body is None else 'POST',
                          headers={'Content-Type': 'application/json'} if body is not None else {})
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get('error', str(exc))
            except ValueError:
                message = str(exc)
            raise BoardError(message, exc.code) from None
        except URLError as exc:
            raise BoardError(f'连不上开发板 {self.base}：{exc.reason}') from None
        return payload if raw else json.loads(payload)

    # ------------------------------------------------------------ 只读
    def health(self):
        """服务状态：api_version、real/simulation、摄像头状态。"""
        return self._request('/api/v1/health')

    def mode(self):
        """当前模式会话：armed、kind（block_grasp / gesture_dance）、session_id、used/max_actions 等。"""
        return self._request('/api/v1/mode')

    def job(self):
        """最近一次动作任务：status 为 running / completed / failed / stopped。"""
        return self._request('/api/v1/job')

    def snapshot(self):
        """开发板摄像头最新一帧 JPEG（不冻结、不读舵机）。"""
        return self._request('/api/v1/frame.jpg', raw=True)

    def wait_for_mode(self, kind, poll_s=1.0, log=print):
        """阻塞直到网页上开启了指定模式，返回 mode() 结果。"""
        announced = False
        while True:
            m = self.mode()
            if m['armed'] and m['kind'] == kind:
                return m
            if not announced:
                log(f'等待网页 {self.base}/modes 开启模式：{kind} ……')
                announced = True
            time.sleep(poll_s)

    def wait_job(self, job_id, poll_s=0.5, timeout_s=180):
        """等待指定任务结束，返回最终 job 字典。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            info = self.job()
            job = info.get('job') or {}
            if job.get('id') == job_id and job.get('status') not in (None, 'running') and not info.get('busy'):
                return job
            time.sleep(poll_s)
        raise BoardError('等待任务结束超时；请看现场与网页状态，不要重复提交')

    # ------------------------------------------------------------ 方块抓取
    def freeze_frame(self, session_id, request_id):
        """让开发板冻结一帧（同时读取六关节），返回 Frame。不运动。

        request_id 来自 mode()['grasp_request']：用户在 /modes 每点一次“开始识别夹取”给一个，只能用一次。"""
        r = self._request('/api/v1/block/frame', dict(session_id=session_id, request_id=request_id), timeout=30)
        return Frame(frame_id=r['frame_id'], jpeg=base64.b64decode(r['jpeg_base64']),
                     image_size=tuple(r['image_size']), joints=r['joints'], captured_at=r['captured_at'])

    def submit_block(self, session_id, request_id, frame_id, detection: BlockDetection):
        """提交识别结果。开发板定位 + 规划（可能要几秒到几十秒）后自动执行，返回 job 信息。"""
        return self._request('/api/v1/block/target',
                             dict(session_id=session_id, request_id=request_id, frame_id=frame_id,
                                  detection=detection.to_dict()),
                             timeout=120)

    def report_block(self, session_id, request_id, message):
        """本次没有提交（没找到方块、识别服务出错等）：告诉开发板，网页上显示原因，本次结束。"""
        return self._request('/api/v1/block/report', dict(session_id=session_id, request_id=request_id,
                                                          message=message))

    # ------------------------------------------------------------ 跳舞
    def dance(self, session_id, routine, label=None, confidence=None):
        """触发一套已授权的跳舞动作。request_id 自动生成，用于防止网络重发导致跳两次。"""
        return self._request('/api/v1/gesture', dict(session_id=session_id, routine=routine, label=label,
                                                     confidence=confidence, request_id=secrets.token_hex(16)))

    # ------------------------------------------------------------ 语音控制模式（用户11-19）
    def _voice(self, path, session_id, text, **fields):
        return self._request(path, dict(session_id=session_id, text=text, request_id=secrets.token_hex(16), **fields))

    def voice_pose(self, session_id, pose, text):
        """回到会话预览时选定的姿态按钮：pose 为 vertical（竖直）或 observe（观察）。"""
        return self._voice('/api/v1/voice/pose', session_id, text, pose=pose)

    def voice_servo(self, session_id, joint, angle, text):
        """把一个舵机转到整数角度；超出会话列出的范围会被开发板拒绝（不截断）。"""
        return self._voice('/api/v1/voice/servo', session_id, text, joint=joint, angle=angle)

    def voice_dance(self, session_id, routine, text, trigger='voice', label=None, confidence=None):
        """语音或语音模式内的表情/手势识别（trigger=expression/gesture，需 label 与 confidence）触发跳舞。"""
        extra = dict(label=label, confidence=confidence) if trigger != 'voice' else {}
        return self._voice('/api/v1/voice/dance', session_id, text, routine=routine, trigger=trigger, **extra)

    def voice_grasp(self, session_id, text):
        """语音“抓方块”：开发板给出一次性 request_id，之后照常 freeze_frame → submit_block / report_block。不运动。"""
        return self._request('/api/v1/voice/grasp', dict(session_id=session_id, text=text))

    # ------------------------------------------------------------ 停止
    def stop(self, session_id=None):
        """请求结束当前模式会话（停止后续动作；已下发的无法撤回）。"""
        return self._request('/api/v1/stop', dict(session_id=session_id))
