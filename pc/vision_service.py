"""PC 端"一键抓取"服务：页面上点按钮即可冻结、识别、提交，不再需要 AI 逐步下令。

设计边界（与 AGENTS.md 一致）：
  * 只能在你于板端 /modes 开启的会话窗口内动作，会话由板端校验（编号/次数/时效/指纹）。
  * 提交前必须有一次成功的"冻结并识别"；冻结超过 --max-age 秒则拒绝提交，要求重新冻结。
  * 只提交像素（corners/center），从不提交关节角；不循环、不自动重试。
  * 页面提交须绑定 freeze_id/frame_id/session_id；同一冻结只提交一次，超时不重发。
  * 冻结原图、关节、识别结果及受理/拒绝结果写入 --out 目录。
  * 页面上"开始抓取"按钮就是当次运动授权；本服务不提供任何自动/定时触发。

用法：
    python pc/vision_service.py --host 0.0.0.0 --port 8099 --board http://192.168.137.100:8080
浏览器打开 http://192.168.137.102:8099/
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from armlink import BoardClient, BoardError  # noqa: E402
from armlink.types import BlockDetection  # noqa: E402
from block_vision.yolo_detector import YoloBlockDetector  # noqa: E402

STATE = {'freeze': None, 'lock': threading.Lock(), 'detector': None,
         'client': None, 'args': None, 'busy': False}
OPERATION_LOCK = threading.Lock()

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>抓取控制台（PC）</title>
<style>
 body{background:#0f1720;color:#e8f0f8;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:22px}
 h1{font-size:19px;margin:0 0 14px}
 .card{background:#16212d;border:1px solid #2b3f55;border-radius:10px;padding:14px;margin-bottom:14px}
 .k{color:#8fb2d0;font-size:13px}
 .v{font-size:15px;margin:2px 0 8px}
 button{font-size:16px;padding:12px 18px;border:0;border-radius:8px;margin:4px 8px 4px 0;cursor:pointer}
 #freeze{background:#2f6fb5;color:#fff}
 #grasp{background:#c0453b;color:#fff}
 #grasp:disabled{background:#4a3230;color:#9b8d8b;cursor:not-allowed}
 #reload{background:#39485a;color:#dfe8f2}
 #msg{white-space:pre-wrap;font-size:13px;color:#b9cde0}
 img{max-width:640px;border-radius:8px;border:1px solid #2b3f55;margin-top:8px}
 .warn{color:#ffb86b}
</style>
<h1>抓取控制台 · PC 侧 A 插件</h1>
<div class="card">
  <div class="k">板端会话（只能在你于 /modes 开启的窗口内动作）</div>
  <div class="v" id="session">读取中…</div>
  <div class="k">板端状态</div>
  <div class="v" id="health">—</div>
</div>
<div class="card">
  <label class="k" for="label">目标颜色</label>
  <select id="label" style="font-size:15px;padding:8px;border-radius:6px;background:#101923;color:#eaf1f8;border:1px solid #49607a;margin:0 0 8px 8px">
    <option value="auto" selected>自动（推荐：四个颜色里取分数最高）</option>
    <option value="block_yellow">黄 block_yellow</option>
    <option value="block_blue">蓝 block_blue</option>
    <option value="block_red">红 block_red</option>
    <option value="block_green">绿 block_green</option>
  </select>
  <div>
    <button id="freeze">① 冻结并识别（不运动）</button>
    <button id="grasp" disabled>② 开始抓取（会运动）</button>
    <button id="reload">刷新状态</button>
  </div>
</div>
<div class="card"><div id="msg">—</div><div id="shot"></div></div>
<script src="/console.js"></script></html>"""


def board(path, body=None, timeout=60):
    args = STATE['args']
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(args.board.rstrip('/') + path, data=data,
                                 method='GET' if body is None else 'POST',
                                 headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get('error', str(exc))
        except ValueError:
            message = str(exc)
        raise BoardError(message, exc.code) from None


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


@contextmanager
def operation():
    if not OPERATION_LOCK.acquire(blocking=False):
        raise BoardError('已有冻结或提交正在处理，请等待；未重复请求')
    with STATE['lock']:
        STATE['busy'] = True
    try:
        yield
    finally:
        with STATE['lock']:
            STATE['busy'] = False
        OPERATION_LOCK.release()


def do_freeze(label):
    with operation():
        # A failed replacement must never leave the previous target usable.
        with STATE['lock']:
            STATE['freeze'] = None
        return freeze_target(label)


def freeze_target(label):
    mode = board('/api/v1/mode')
    if not (mode.get('armed') and mode.get('kind') == 'block_grasp' and mode.get('session_id')):
        raise BoardError('没有已开启的 block_grasp 会话：请先在板端 /modes 开启')
    health = board('/api/v1/health')
    if health.get('mode') != 'real':
        raise BoardError(f"板端为 {health.get('mode')} 模式，冻结会得到假关节读数")
    if not health.get('camera', {}).get('connected'):
        raise BoardError('板端相机未连接')
    session = mode['session_id']
    frozen = board('/api/v1/block/frame', {'session_id': session}, timeout=60)
    frame_id = frozen.get('frame_id') or frozen.get('id')
    received_at = time.time()  # Include inference and evidence writing in preview age.
    if not isinstance(frame_id, str) or not frame_id:
        raise BoardError('冻结响应缺少 frame_id，未建立可提交目标')
    raw = base64.b64decode(frozen['jpeg_base64'])
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None or frozen.get('image_size') != [image.shape[1], image.shape[0]]:
        raise BoardError('冻结原图无法解码或尺寸不符，未建立可提交目标')
    detector = STATE['detector']
    result = detector.analyze(image)
    candidates = detector.candidates(result)
    if label == 'auto':
        mine = sorted(candidates, key=lambda d: d.score, reverse=True)
    else:
        mine = sorted((d for d in candidates if d.label == label), key=lambda d: d.score, reverse=True)
    chosen = mine[0] if mine else None
    detected = [f"{d.label}:{d.score:.3f}" for d in sorted(candidates, key=lambda d: d.score, reverse=True)]
    freeze_id = uuid.uuid4().hex
    evidence = STATE['args'].out / ('freeze_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + freeze_id)
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / 'raw.jpg').write_bytes(raw)
    write_json(evidence / 'capture.json', dict(
        {k: v for k, v in frozen.items() if k != 'jpeg_base64'},
        session_id=session, freeze_id=freeze_id, frame_id=frame_id,
        received_at_pc=received_at, raw_sha256=hashlib.sha256(raw).hexdigest()))
    write_json(evidence / 'result.json', result)
    payload = chosen.to_dict() if chosen is not None else None
    write_json(evidence / 'detection.json', payload)
    if chosen is None:
        # 宁可报错，也不留空载荷：前端据此保持"② 开始抓取"不可点。
        detail = '、'.join(detected) if detected else '画面里没有任何积木候选'
        raise BoardError(f'这一帧没有可用候选（检出：{detail}）；'
                         f'请核对原图与复核原因；记录：{evidence}')
    ok, encoded = cv2.imencode('.png', overlay(image, result))
    if ok:
        (evidence / 'overlay.png').write_bytes(encoded.tobytes())
    with STATE['lock']:
        STATE['freeze'] = dict(session=session, frame_id=frame_id, freeze_id=freeze_id,
                               payload=payload, at=received_at, label=chosen.label, evidence=evidence)
    return dict(frame_id=frame_id, freeze_id=freeze_id, session_id=session, evidence_dir=str(evidence),
                joints=[f"ID{j['id']}={j['angle']}" for j in frozen.get('joints') or []],
                candidates=len(candidates),
                labels=detected,
                chosen=dict(label=chosen.label, score=round(chosen.score, 6),
                            center=[round(v, 3) for v in chosen.center]),
                payload=chosen.to_dict(),
                overlay=base64.b64encode(encoded.tobytes()).decode() if ok else None,
                min_score=mode.get('min_score'))


def do_grasp(fields):
    with operation():
        return submit_target(fields)


def submit_target(fields):
    args = STATE['args']
    with STATE['lock']:
        frozen = STATE['freeze']
    if not frozen or not frozen['payload']:
        raise BoardError('还没有可用的冻结结果：请先点"冻结并识别"')
    if (not isinstance(fields, dict) or fields.get('freeze_id') != frozen['freeze_id'] or
            fields.get('frame_id') != frozen['frame_id'] or fields.get('session_id') != frozen['session']):
        raise BoardError('页面预览与当前冻结结果不一致，请刷新8099控制台并重新冻结；未提交')
    age = time.time() - frozen['at']
    if age > args.max_age:
        raise BoardError(f'冻结已过期（{age:.0f}s > {args.max_age}s）：请重新冻结')
    mode = board('/api/v1/mode')
    if not mode.get('armed') or mode.get('session_id') != frozen['session']:
        raise BoardError('会话已变化或未开启：请重新冻结')
    detection = BlockDetection(corners=[tuple(c) for c in frozen['payload']['corners']],
                               center=tuple(frozen['payload']['center']),
                               score=float(frozen['payload']['score']),
                               label=frozen['payload'].get('label'))
    request = dict(session_id=frozen['session'], frame_id=frozen['frame_id'], detection=detection.to_dict())
    evidence = frozen['evidence']
    write_json(evidence / 'request.json', request)
    # Consume before sending: duplicate requests and timeouts must not resend motion.
    with STATE['lock']:
        STATE['freeze'] = None
    try:
        response = board('/api/v1/block/target', request, timeout=180)
    except Exception as exc:
        status = getattr(exc, 'status', None)
        rejected = status in (400, 403, 404, 409, 410, 422)
        record = dict(status='rejected' if rejected else 'unknown', error=str(exc),
                      http_status=status, frame_id=frozen['frame_id'], age_s=round(age, 3),
                      at_pc=datetime.now(timezone.utc).isoformat())
        try:
            write_json(evidence / 'submission_result.json', record)
        except OSError as log_error:
            sys.stderr.write(f'抓取结果记录失败：{evidence}: {log_error}\n')
        sys.stderr.write(json.dumps(record, ensure_ascii=False) + '\n')
        prefix = '规划/请求被拒绝' if rejected else '提交状态未知，请先查询板端任务，勿重复提交'
        raise BoardError(f'{prefix}：{exc}；记录：{evidence}', status) from exc
    out = dict(session=frozen['session'], frame_id=frozen['frame_id'],
               age_s=round(age, 1), response=response, evidence_dir=str(evidence))
    try:
        write_json(evidence / 'submission_result.json', dict(status='accepted', **out))
    except OSError as log_error:
        out['warning'] = '请求已受理，但结果记录失败：' + str(log_error)
        sys.stderr.write(out['warning'] + '\n')
    return out


def overlay(image, result):
    from block_vision.preview import overlay as draw
    return draw(image, result)


ALLOWED_ORIGINS = {'http://192.168.137.100:8080', 'http://192.168.137.102:8099',
                   'http://127.0.0.1:8099', 'http://localhost:8099'}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, mime='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        origin = self.headers.get('Origin')
        allow = origin if origin in ALLOWED_ORIGINS else 'http://127.0.0.1:8099'
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', allow)
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        return origin is None or origin in ALLOWED_ORIGINS

    def do_OPTIONS(self):
        self._send(200, b'')

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._send(200, PAGE, 'text/html; charset=utf-8')
            return
        if self.path == '/console.js':
            self._send(200, Path(__file__).with_name('vision_console.js').read_bytes(), 'text/javascript; charset=utf-8')
            return
        if self.path == '/api/status':
            try:
                self._send(200, dict(busy=STATE['busy'], health=board('/api/v1/health', timeout=6),
                                     mode=board('/api/v1/mode', timeout=6),
                                     job=board('/api/v1/job', timeout=6)))
            except Exception as exc:  # noqa: BLE001
                self._send(200, dict(error=str(exc)))
            return
        self._send(404, dict(error='无此接口'))

    def do_POST(self):
        if not self._origin_ok():
            self._send(403, dict(error='来源不在允许列表：' + str(self.headers.get('Origin'))))
            return
        try:
            path, _, query = self.path.partition('?')
            if path == '/api/freeze':
                wanted = STATE['args'].label
                for part in query.split('&'):
                    key, _, value = part.partition('=')
                    if key == 'label' and value:
                        wanted = urllib.parse.unquote(value)
                if wanted not in ('auto', 'block_blue', 'block_green', 'block_red', 'block_yellow'):
                    self._send(200, dict(error=f'不支持的目标颜色：{wanted}'))
                    return
                self._send(200, do_freeze(wanted))
            elif path == '/api/grasp':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise BoardError('提交须包含当前冻结编号；请刷新8099控制台并重新冻结')
                self._send(200, do_grasp(json.loads(self.rfile.read(length))))
            else:
                self._send(404, dict(error='无此接口'))
        except BoardError as exc:
            self._send(200, dict(error=str(exc)))
        except Exception as exc:  # noqa: BLE001
            self._send(200, dict(error=f'{type(exc).__name__}: {exc}'))

    def log_message(self, fmt, *args):
        if '/api/status' in (args[0] if args else ''):
            return
        sys.stderr.write('%s %s\n' % (datetime.now().strftime('%H:%M:%S'), fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8099)
    parser.add_argument('--board', default='http://192.168.137.100:8080')
    parser.add_argument('--label', default='auto',
                        choices=['auto', 'block_blue', 'block_green', 'block_red', 'block_yellow'],
                        help='auto=自动在四个颜色里取分数最高；也可固定某一种颜色')
    parser.add_argument('--max-age', type=int, default=90)
    parser.add_argument('--out', default=str(Path(__file__).resolve().parent /
                                             'block_vision/weights/grasp_service'))
    args = parser.parse_args()
    args.out = Path(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    STATE['args'] = args
    STATE['detector'] = YoloBlockDetector()

    class SingleInstanceServer(ThreadingHTTPServer):
        # 关键：Windows 下 SO_REUSEADDR 允许两个进程绑同一端口，会导致
        # "冻结请求进 A 进程、抓取请求进 B 进程"，表现为按钮点了没反应。
        # 关掉复用，第二个实例会直接启动失败。
        allow_reuse_address = False

    print(f'模型已加载；服务 http://{args.host}:{args.port}/ ；板端 {args.board}', flush=True)
    try:
        server = SingleInstanceServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f'端口 {args.port} 已被占用（可能已有一个控制台在跑）：{exc}；'
                         f'请先关掉旧实例，不要同时启动多个') from None
    print(f'已监听 {args.host}:{args.port}（单实例模式）', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
