"""表情/手势触发跳舞（电脑端运行）。

先在网页 http://192.168.137.100:8080/modes 开启“表情手势跳舞”，再运行：
    python pc/run_gesture_dance.py
默认用电脑端手势/微笑模型读取开发板摄像头；也可用 --source webcam 指定电脑摄像头，
键盘联调则运行 `python pc/run_gesture_dance.py --recognizer gesture.keyboard:KeyboardRecognizer --source none`。
网页模式仍须由现场人员检查并开启。

同一标签累计 --stable 次有效识别且置信度 ≥ --threshold 才算一次触发；允许中间漏检1帧。
触发后等跳完 + 冷却再接受下一次。
"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from armlink import BoardClient, BoardError, DEFAULT_BOARD  # noqa: E402
from frames import load_class, open_source  # noqa: E402


def parse_map(text):
    mapping = {}
    for item in filter(None, (x.strip() for x in text.split(','))):
        label, _, routine = item.partition('=')
        if not routine:
            raise SystemExit('--map 格式：标签=动作，多个用逗号分隔，例如 smile=course,wave=music')
        mapping[label.strip()] = routine.strip()
    return mapping


class Debouncer:
    """同一标签累计命中 stable 次才输出一次；可配置容忍短暂漏检。"""

    def __init__(self, stable=3, threshold=0.6, max_missed_frames=0):
        self.stable, self.threshold = stable, threshold
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.label, self.count, self.fired = None, 0, False
        self.missed_frames = 0

    def feed(self, event):
        label = event.label if event is not None and event.confidence >= self.threshold else None
        if label is None:
            if self.label is not None:
                self.missed_frames += 1
                if self.missed_frames > self.max_missed_frames:
                    self.label, self.count, self.fired = None, 0, False
                    self.missed_frames = 0
            return None
        if label != self.label:
            self.label, self.count, self.fired = label, 0, False
        self.missed_frames = 0
        self.count += 1
        if self.count >= self.stable and not self.fired:
            self.fired = True
            return event
        return None


def run(client, recognizer, source, mapping, stable=3, threshold=0.2, log=print, max_seconds=None):
    log('等待网页开启手势模式；模式开启前不会读取画面或识别。')
    mode = client.wait_for_mode('gesture_dance', log=log)
    session = mode['session_id']
    unknown = sorted(set(mapping.values()) - set(mode['routines']))
    if unknown:
        log(f'警告：网页本次没有授权这些动作，触发时会被拒绝：{unknown}（已授权：{mode["routines"]}）')
    log(f"会话 {session}：最多 {mode['max_actions']} 次，间隔 ≥ {mode['cooldown_s']} 秒；映射 {mapping}")
    debounce = Debouncer(stable, threshold, max_missed_frames=1)
    started = time.monotonic()
    last_check = 0
    while max_seconds is None or time.monotonic() - started < max_seconds:
        if time.monotonic() - last_check > 1:
            last_check = time.monotonic()
            mode = client.mode()
            if not mode['armed'] or mode['session_id'] != session:
                log('会话已结束：' + str(mode.get('message') or '网页已关闭或次数用完'))
                return
        image = source.read()
        detected = recognizer.recognize(image)
        event = debounce.feed(detected)
        if detected is not None and not debounce.fired:
            log(f'识别候选 {detected.label}（{detected.confidence:.2f}）：{debounce.count}/{stable}')
        if event is None:
            continue
        log(f'识别确认：{event.label}（{event.confidence:.2f}）')
        routine = mapping.get(event.label)
        if routine is None:
            log(f'识别到 {event.label}，但没有映射到动作，忽略')
            continue
        try:
            result = client.dance(session, routine, event.label, event.confidence)
        except BoardError as exc:
            log(f'{event.label} → {routine} 未执行：{exc}')
            continue
        log(f"{event.label}（{event.confidence:.2f}）→ 跳 {routine}，约 {result['duration_ms'] / 1000:.1f} 秒")
        job = client.wait_job(result['job']['id'])
        log(f"跳舞结束：{job['status']} {job.get('message') or job.get('error') or ''}")
        if job['status'] == 'failed':
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--board', default=DEFAULT_BOARD)
    parser.add_argument('--recognizer', default='gesture.recognizer:DanceRecognizer', help='识别器类 模块:类名')
    parser.add_argument('--source', default='board', help='board（开发板摄像头，默认）/ webcam / webcam1 / none')
    parser.add_argument('--map', default='smile=course,palm=music', help='标签=动作,标签=动作')
    parser.add_argument('--stable', type=int, default=3, help='有效识别次数；允许中间漏检1帧')
    parser.add_argument('--threshold', type=float, default=0.2, help='置信度下限')
    args = parser.parse_args()
    client = BoardClient(args.board)
    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    log('开发板：' + str(client.health()))
    log('图像来源：' + args.source)
    log('识别器：' + args.recognizer)
    log(f'触发设置：阈值={args.threshold}，有效识别次数={args.stable}，映射={args.map}')
    recognizer = load_class(args.recognizer)
    source = open_source(args.source, client)
    try:
        run(client, recognizer, source, parse_map(args.map), args.stable, args.threshold, log=log)
    except KeyboardInterrupt:
        log('Ctrl+C：请求结束会话（已下发的动作无法撤回）')
        try:
            client.stop()
        except BoardError as exc:
            log(str(exc))
    finally:
        source.close()
        recognizer.close()


if __name__ == '__main__':
    main()
