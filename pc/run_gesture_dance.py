"""表情/手势触发跳舞（电脑端运行）。

先在网页 http://192.168.137.100:8080/modes 开启“表情手势跳舞”，再运行：
    python pc/run_gesture_dance.py --recognizer my_model:MyRecognizer --map smile=snake,wave=circle
    python pc/run_gesture_dance.py --recognizer gesture.keyboard:KeyboardRecognizer --source none --map smile=snake
--source 选帧来源：webcam（电脑摄像头，默认）/ board（开发板摄像头）/ none（键盘模拟）。

同一标签连续 --stable 帧且置信度 ≥ --threshold 才算一次触发；触发后等跳完 + 冷却再接受下一次。
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
            raise SystemExit('--map 格式：标签=动作，多个用逗号分隔，例如 smile=snake,wave=circle')
        mapping[label.strip()] = routine.strip()
    return mapping


class Debouncer:
    """同一标签连续 stable 帧才输出一次；输出后需要先看到别的结果才会再次输出。"""

    def __init__(self, stable=3, threshold=0.6):
        self.stable, self.threshold = stable, threshold
        self.label, self.count, self.fired = None, 0, False

    def feed(self, event):
        label = event.label if event is not None and event.confidence >= self.threshold else None
        if label != self.label:
            self.label, self.count, self.fired = label, 0, False
        if label is None:
            return None
        self.count += 1
        if self.count >= self.stable and not self.fired:
            self.fired = True
            return event
        return None


def run(client, recognizer, source, mapping, stable=3, threshold=0.6, log=print, max_seconds=None):
    mode = client.wait_for_mode('gesture_dance', log=log)
    session = mode['session_id']
    unknown = sorted(set(mapping.values()) - set(mode['routines']))
    if unknown:
        log(f'警告：网页本次没有授权这些动作，触发时会被拒绝：{unknown}（已授权：{mode["routines"]}）')
    log(f"会话 {session}：最多 {mode['max_actions']} 次，间隔 ≥ {mode['cooldown_s']} 秒；映射 {mapping}")
    debounce = Debouncer(stable, threshold)
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
        event = debounce.feed(recognizer.recognize(image))
        if event is None:
            continue
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
    parser.add_argument('--recognizer', default='gesture.keyboard:KeyboardRecognizer', help='识别器类 模块:类名')
    parser.add_argument('--source', default='webcam', help='webcam / webcam1 / board / none')
    parser.add_argument('--map', default='smile=snake,wave=circle', help='标签=动作,标签=动作')
    parser.add_argument('--stable', type=int, default=3, help='连续多少帧相同才触发')
    parser.add_argument('--threshold', type=float, default=0.6, help='置信度下限')
    args = parser.parse_args()
    client = BoardClient(args.board)
    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    log('开发板：' + str(client.health()))
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
