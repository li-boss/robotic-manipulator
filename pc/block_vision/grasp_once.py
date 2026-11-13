"""单次真实抓取入口（电脑端）。冻结一帧 → 正式 A 插件识别 → 展示 → 由人确认后提交一次。

三种模式：
  --stage            冻结+识别+保存证据并打印，**不提交**（给现场人看）
  --submit <目录>    用该目录里保存的 session_id/frame_id/detection 提交一次
  （无参数）          冻结+识别+打印，终端里回车确认后提交（需要真 TTY）

硬规则：
  * 不循环、不自动重试、不自动换目标；一次提交后退出。
  * 只提交像素（corners/center），从不提交关节角。
  * 提交沿用同一 session_id / frame_id / detection；冻结失效只重新冻结并重新展示。
  * 网络错误/超时 = 状态未知：只查 /api/v1/job，绝不重复下发。
  * 板端必须是 real 模式；模拟模式的关节读数是假值，直接拒绝。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import base64

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armlink import BoardClient, BoardError, DEFAULT_BOARD  # noqa: E402
from armlink.types import BlockDetection  # noqa: E402
from block_vision.preview import overlay  # noqa: E402
from block_vision.yolo_detector import YoloBlockDetector  # noqa: E402

STALE_HINTS = ('姿态', '冻结', '重新冻结', '已改变', '过期', '失效')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def describe_mode(mode):
    if not mode.get('armed'):
        return '未开启任何模式'
    parts = [f"kind={mode.get('kind')}", f"session={mode.get('session_id')}", f"busy={mode.get('busy')}"]
    for key in ('task_mode', 'max_actions', 'used', 'expires_in', 'min_score'):
        if key in mode:
            parts.append(f'{key}={mode[key]}')
    return ' '.join(parts)


def analyze(image, detector, label):
    result = detector.analyze(image)
    candidates = detector.candidates(result)
    rows = []
    for target in result['targets']:
        face = target['top_face']
        rows.append(dict(label=target['raw_label'], conf=target['confidence'],
                         plugin=target['plugin_output'], reason=face['reason'],
                         center=face['candidate_point']))
    mine = sorted((d for d in candidates if d.label == label), key=lambda d: d.score, reverse=True)
    return result, candidates, rows, mine


def print_block(tag, frame_id, joints, result, candidates, rows, mine, label, min_score, out_dir):
    print(f'\n===== {tag} =====')
    print(f'frame_id  : {frame_id}')
    print(f'图片尺寸  : {result["image_width"]}x{result["image_height"]}'
          f'  后端 {result["backend_ms"]}ms  顶面 {result["top_face_ms"]}ms')
    if joints:
        print(f'关节读数  : {len(joints)} 个，全部可读={all(j.get("read_ok") for j in joints)}')
        print('   ' + '  '.join(f"ID{j.get('id')}={j.get('angle')}({j.get('read_ok')})" for j in joints))
    print(f'YOLO 框   : {len(result["targets"])}   插件候选: {len(candidates)}')
    for i, row in enumerate(rows, 1):
        flag = 'CAND' if row['plugin'] == 'candidate' else f"rej:{row['reason'] or row['plugin']}"
        print(f"   {i}. {row['label']:14s} conf={row['conf']:.6f}  {flag}  center={row['center']}")
    if not mine:
        print(f'\n>>> 没有 {label} 的合格候选，未提交。')
        return None
    chosen = mine[0]
    print(f"\n所选目标  : {chosen.label}  score={chosen.score:.6f}  "
          f"center=({chosen.center[0]:.3f}, {chosen.center[1]:.3f})")
    print(f'顶面四角  : [[{chosen.corners[0][0]:.1f},{chosen.corners[0][1]:.1f}] '
          f'[{chosen.corners[1][0]:.1f},{chosen.corners[1][1]:.1f}] '
          f'[{chosen.corners[2][0]:.1f},{chosen.corners[2][1]:.1f}] '
          f'[{chosen.corners[3][0]:.1f},{chosen.corners[3][1]:.1f}]]')
    if chosen.score < min_score:
        print(f'>>> 注意：score 低于会话下限 {min_score}，板端会拒绝。')
    print('将提交的 detection（仅像素）：')
    print('   ' + json.dumps(chosen.to_dict(), ensure_ascii=False))
    print(f'证据目录  : {out_dir}')
    return chosen


def save_evidence(out_dir, raw_jpeg, image, result, meta, payload, session):
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / 'raw.jpg').write_bytes(raw_jpeg)
    (out_dir / 'result.json').write_text(json.dumps(result | meta, ensure_ascii=False, indent=2),
                                         encoding='utf-8')
    ok, encoded = cv2.imencode('.png', overlay(image, result))
    if ok:
        encoded.tofile(str(out_dir / 'overlay.png'))
    meta = dict(meta, session_id=session, staged_at=now_iso())
    (out_dir / 'capture_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
    (out_dir / 'submission.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                             encoding='utf-8')
    print(f'已保存：raw.jpg / result.json / overlay.png / capture_meta.json / submission.json')


def unknown_state(client, note):
    print(f'\n!!! {note}')
    print('!!! 不要重复运行提交。先查任务状态：')
    for _ in range(10):
        try:
            info = client.job()
            print('    /api/v1/job =', json.dumps(info, ensure_ascii=False))
            job = info.get('job') or {}
            if job and job.get('status') not in (None, 'running') and not info.get('busy'):
                return job
        except Exception as error:  # noqa: BLE001
            print('    查询失败:', error)
        time.sleep(2)
    print('!!! 10 次查询仍无确定状态：请到网页与现场确认，勿重发。')
    return None


def preflight(client):
    health = client.health()
    if health.get('api_version') != 1 or health.get('service') != 'mechanical-arm-board':
        raise SystemExit(f'不是新版板端服务：{health}')
    if health.get('mode') != 'real':
        raise SystemExit(f'板端当前是 {health.get("mode")} 模式，关节读数会是假值；请用 --enable-real-control 重启板端')
    if not health.get('camera', {}).get('connected'):
        raise SystemExit(f'板端相机未连接：{health.get("camera")}')
    print('板端 health:', json.dumps(health, ensure_ascii=False))
    mode = client.mode()
    print('板端 mode  :', describe_mode(mode))
    if not (mode.get('armed') and mode.get('kind') == 'block_grasp' and mode.get('session_id')):
        raise SystemExit('没有已开启的 block_grasp 会话（请在浏览器 /modes 开启后再运行）')
    return mode


def submit_once(client, session, frame_id, payload, out_dir, wait):
    again = client.mode()
    if again.get('session_id') != session:
        print(f'会话已变：{describe_mode(again)}；未提交。')
        return 4
    if again.get('busy'):
        print('板端忙，未提交。')
        return 4
    detection = BlockDetection(corners=[tuple(c) for c in payload['corners']],
                               center=tuple(payload['center']),
                               score=float(payload['score']), label=payload.get('label'))
    print(f'\n提交：session={session}  frame_id={frame_id}  score={detection.score:.6f}')
    try:
        response = client.submit_block(session, frame_id, detection)
    except BoardError as error:
        if error.status is not None:
            print(f'板端拒绝（{error.status}）：{error}；未执行。')
            return 5
        unknown_state(client, f'提交时网络异常：{error}')
        return 6
    except Exception as error:  # noqa: BLE001
        unknown_state(client, f'提交时异常：{type(error).__name__}: {error}')
        return 6
    print('提交已受理：', json.dumps(response, ensure_ascii=False, indent=2))
    job = (response.get('job') or {}).get('id')
    if job and wait:
        try:
            final = client.wait_job(job, timeout_s=240)
            print('任务结束：', json.dumps(final, ensure_ascii=False))
        except BoardError as error:
            print('等待任务结束失败：', error)
            unknown_state(client, '等待超时，状态未知')
    if out_dir:
        (Path(out_dir) / 'board_response.json').write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


def freeze_and_show(client, detector, label, out_dir, kernel_hint=''):
    frozen = client._request('/api/v1/block/frame', dict(session_id=client.mode()['session_id']),
                             timeout=60)
    frame_id = frozen.get('frame_id') or frozen.get('id')
    if not frame_id:
        raise SystemExit(f'冻结响应里没有 frame_id/id：{sorted(frozen)}')
    raw_jpeg = base64.b64decode(frozen['jpeg_base64'])
    image = cv2.imdecode(np.frombuffer(raw_jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit('冻结响应不是可解码的 JPEG')
    joints = frozen.get('joints') or []
    meta = dict(frame_id=frame_id, source_type='new_board_frozen_frame', joints=joints,
                board_captured_at=frozen.get('captured_at'),
                image_size=frozen.get('image_size') or [image.shape[1], image.shape[0]],
                received_at_pc=now_iso())
    result, candidates, rows, mine = analyze(image, detector, label)
    mode = client.mode()
    chosen = print_block(f'冻结结果 {kernel_hint}', frame_id, joints, result, candidates, rows,
                         mine, label, float(mode.get('min_score', 0.5)), out_dir)
    if chosen is None:
        save_evidence(out_dir, raw_jpeg, image, result, meta, {}, mode['session_id'])
        return None, None, None
    save_evidence(out_dir, raw_jpeg, image, result, meta, chosen.to_dict(), mode['session_id'])
    return frame_id, chosen.to_dict(), mode['session_id']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--board', default=DEFAULT_BOARD)
    parser.add_argument('--label', default='block_yellow')
    parser.add_argument('--out')
    parser.add_argument('--image', help='只分析本地图片，不连板端')
    parser.add_argument('--stage', action='store_true', help='冻结+识别+保存，不提交')
    parser.add_argument('--submit', metavar='DIR', help='用该证据目录提交一次')
    parser.add_argument('--yes', action='store_true', help='跳过回车确认')
    args = parser.parse_args()

    detector = YoloBlockDetector()

    if args.image:
        raw = Path(args.image).read_bytes()
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        result, candidates, rows, mine = analyze(image, detector, args.label)
        print_block('本地图片模式（不连板端）', None, None, result, candidates, rows, mine,
                    args.label, 0.0, None)
        return 0

    client = BoardClient(args.board, timeout=15)

    if args.submit:
        out_dir = Path(args.submit).resolve()
        meta = json.loads((out_dir / 'capture_meta.json').read_text(encoding='utf-8'))
        payload = json.loads((out_dir / 'submission.json').read_text(encoding='utf-8'))
        mode = preflight(client)
        if mode['session_id'] != meta['session_id']:
            print(f"会话不符：当前 {mode['session_id']}，证据 {meta['session_id']}；未提交。")
            return 4
        print(f"沿用证据：frame_id={meta['frame_id']}  recorded_at={meta['received_at_pc']}")
        return submit_once(client, meta['session_id'], meta['frame_id'], payload, out_dir, wait=True)

    mode = preflight(client)
    session = mode['session_id']
    out_dir = Path(args.out).resolve() if args.out else \
        Path(__file__).resolve().parent / 'weights' / f'grasp_once_{datetime.now():%Y%m%d_%H%M%S}'

    frame_id, payload, session = freeze_and_show(client, detector, args.label, out_dir)
    if payload is None:
        return 2
    if args.stage:
        print('\n[stage 模式] 未提交。核对上面内容后执行：')
        print(f'  python pc/block_vision/grasp_once.py --submit "{out_dir}"')
        return 0
    if not args.yes:
        print('\n回车 = 用同一 session_id / frame_id / detection 提交；Ctrl+C = 放弃')
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print('已放弃，未提交。')
            return 3
    return submit_once(client, session, frame_id, payload, out_dir, wait=True)


if __name__ == '__main__':
    raise SystemExit(main())
