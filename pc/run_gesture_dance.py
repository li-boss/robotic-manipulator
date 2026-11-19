"""表情/手势触发跳舞（电脑端运行）。

先在网页 http://192.168.137.100:8080/modes 开启“表情手势跳舞”，再运行：
    python pc/run_gesture_dance.py            # 按 pc/lan_config.json：有 gesture_url 就把画面发给局域网识别服务
    python pc/run_gesture_dance.py --local    # 忽略配置，用本机 --recognizer 模型
画面默认来自开发板摄像头；也可用 --source webcam 指定 Mac 摄像头。
键盘联调：`python pc/run_gesture_dance.py --local --recognizer gesture.keyboard:KeyboardRecognizer --source none`。
地址写在 pc/lan_config.json（换局域网只改这个文件）；命令行 --board / --gesture-url 可临时覆盖。
网页模式仍须由现场人员检查并开启。

同一标签累计 --stable 次有效识别且置信度 ≥ --threshold 才算一次触发；允许中间漏检1帧。
触发后等跳完 + 冷却再接受下一次。
"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from armlink import BoardClient, BoardError  # noqa: E402
from frames import load_class, open_source  # noqa: E402
import lan_config  # noqa: E402
from remote_recognizer import RemoteProtocolError, RemoteRecognizeError, RemoteRecognizer  # noqa: E402


def parse_map(text):
    mapping = {}
    for item in filter(None, (x.strip() for x in text.split(','))):
        label, _, routine = item.partition('=')
        if not routine:
            raise SystemExit('--map 格式：标签=动作，多个用逗号分隔，例如 smile=COURSE_DANCE_ROUTINE,wave=MUSIC_DANCE_ROUTINE')
        mapping[label.strip()] = routine.strip()
    return mapping


class Debouncer:
    """同一标签累计命中 stable 次才输出一次；可配置容忍短暂漏检。"""

    def __init__(self, stable=3, threshold=0.6, max_missed_frames=0):
        self.stable, self.threshold = stable, threshold
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.label, self.count, self.fired = None, 0, False
        self.missed_frames = 0

    def reset(self):
        self.label, self.count, self.fired, self.missed_frames = None, 0, False, 0

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
        try:
            detected = recognizer.recognize(image)
        except RemoteProtocolError as exc:
            log('识别服务回复不合协议，程序停止（不触发任何动作）：' + str(exc))
            return
        except RemoteRecognizeError as exc:
            # 网络问题：本帧当作没识别到（防抖计数随之清零），1 秒后再取帧，不会因此触发动作。
            log('识别服务暂时不可用，本帧按“未识别”处理：' + str(exc))
            detected = None
            debounce.reset()
            time.sleep(1)
            continue
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
    parser.add_argument('--config', default=str(lan_config.PATH), help='局域网地址配置文件（默认 pc/lan_config.json）')
    parser.add_argument('--board', help='开发板地址（默认取配置文件 board）')
    parser.add_argument('--gesture-url', metavar='URL', help='局域网表情手势识别服务（默认取配置文件 gesture_url；jjt-gesture/1）')
    parser.add_argument('--local', action='store_true', help='不用局域网识别服务，改用本机 --recognizer')
    parser.add_argument('--recognizer', default='gesture.recognizer:DanceRecognizer', help='本机识别器类 模块:类名（--local 或未配置地址时用）')
    parser.add_argument('--source', default='board', help='board（开发板摄像头，默认）/ webcam / webcam1 / none')
    parser.add_argument('--map', default='smile=COURSE_DANCE_ROUTINE,palm=MUSIC_DANCE_ROUTINE', help='标签=动作,标签=动作')
    parser.add_argument('--stable', type=int, default=3, help='有效识别次数；允许中间漏检1帧')
    parser.add_argument('--threshold', type=float, default=0.2, help='置信度下限')
    args = parser.parse_args()
    config = lan_config.load(args.config)
    board = args.board or config['board']
    url = '' if args.local else (args.gesture_url if args.gesture_url is not None else config['gesture_url'])
    client = BoardClient(board)
    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    log(f'配置：{args.config}；开发板 {board}')
    log('开发板：' + str(client.health()))
    log('图像来源：' + args.source)
    log('识别器：' + (f'局域网识别服务 {url}' if url else '本机 ' + args.recognizer))
    log(f'触发设置：阈值={args.threshold}，有效识别次数={args.stable}，映射={args.map}')
    if url:
        recognizer = RemoteRecognizer(url, log=log)
        try:
            log('识别服务：' + str(recognizer.health()))
        except (RemoteRecognizeError, RemoteProtocolError) as exc:
            log(str(exc) + '（地址不对就改 ' + args.config + ' 的 gesture_url）');return
    else:
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
