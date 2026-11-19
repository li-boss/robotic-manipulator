"""语音控制模式（电脑端运行，用户2026-11-19要求）。

先在网页 http://192.168.137.100:8080/modes 的“语音控制”页预览并开启会话，再在 Mac 上运行：
    .venv-mac/bin/python pc/voice_mode/run_voice_mode.py            # 麦克风：按回车开始说，再按回车结束
    .venv-mac/bin/python pc/voice_mode/run_voice_mode.py --wav a.wav b.wav   # 依次处理几个录音文件
    .venv-mac/bin/python pc/voice_mode/run_voice_mode.py --text     # 跳过语音识别，直接输入文字（联调用）
本地无硬件联调：先 python board/simulate.py，再加 --board http://127.0.0.1:8092 --local。

每句话：WeNet 转文字 → 语音同学的分类器（pc/voice_control）判断是哪条命令 → 本程序调用开发板语音模式接口。
命令即 pc/voice_control/text_classification/commands.json 的八类：音乐舞蹈、课程舞蹈、竖直姿态、观察姿态、
N号舵机转到X度、抓方块、开始表情识别、开始手势识别。每句话最多触发一次动作，不自动重试；
开发板只执行会话预览里列出的动作。方块识别与表情手势识别服务地址取自 pc/lan/lan_config.json。
Ctrl+C 只停止本程序并请求结束会话，已下发的动作无法撤回，不是急停。
"""
import argparse
from pathlib import Path
import sys
import tempfile
import time

PC = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PC), str(PC.parent)]
from armlink import BoardClient, BoardError  # noqa: E402
from armlink.frames import load_class, open_source  # noqa: E402
from block_vision.run_block_grasp import attempt as grasp_attempt  # noqa: E402
from gesture.run_gesture_dance import Debouncer, parse_map  # noqa: E402
from lan import lan_config  # noqa: E402
from lan.remote_detector import RemoteDetectError, RemoteDetector  # noqa: E402
from lan.remote_recognizer import RemoteProtocolError, RemoteRecognizeError, RemoteRecognizer  # noqa: E402
from voice_mode.servo_text import parse_servo_command  # noqa: E402

KIND = 'voice_control'
EXPRESSION_LABELS = {'smile'}        # 表情识别只接受这些标签；其余标签算手势
PLACE_WORDS = ('放到', '放进', '放入', '投放到')
COLORS = {'红': 'red', '黄': 'yellow', '蓝': 'blue', '绿': 'green'}


class StopProgram(Exception):
    """识别服务回复不合协议等：停止本程序（不触发任何动作）。"""


def requested_color(text):
    """与语音同学 actions._requested_color 相同的规则：说了一种颜色只抓该颜色，说了多种不提交。"""
    colors = {name for word, name in COLORS.items()
              if f'{word}色' in text or f'{word}方块' in text or f'{word}积木' in text}
    if len(colors) > 1:
        raise ValueError('语音中出现多种颜色，无法确定要抓的方块')
    return next(iter(colors), None)


class ColorFilter:
    """只保留指定颜色的候选；标签写法兼容 yellow 与 block_yellow。"""

    def __init__(self, detector, color):
        self.detector, self.color = detector, color
        if hasattr(detector, 'detect_jpeg'):
            self.detect_jpeg = lambda *a: self._keep(detector.detect_jpeg(*a))

    def _keep(self, found):
        return [d for d in found if d.label in (self.color, f'block_{self.color}')]

    def detect(self, image):
        return self._keep(self.detector.detect(image))


class VoiceMode:
    def __init__(self, client, log=print, detector=None, recognizer=None, source=None,
                 mapping=None, recog_seconds=20.0, stable=3, threshold=0.2):
        self.client, self.log = client, log
        self.detector, self.recognizer, self.source = detector, recognizer, source
        self.mapping = mapping or {}
        self.recog_seconds, self.stable, self.threshold = recog_seconds, stable, threshold
        self.session = None
        self.handlers = {
            'MUSIC_DANCE_ROUTINE': lambda text: self.dance('MUSIC_DANCE_ROUTINE', text),
            'COURSE_DANCE_ROUTINE': lambda text: self.dance('COURSE_DANCE_ROUTINE', text),
            'POSE_VERTICAL': lambda text: self.pose('vertical', text),
            'POSE_OBSERVE': lambda text: self.pose('observe', text),
            'SET_SERVO_ABS_ANGLE': self.servo,
            'START_BLOCK_GRASP': self.grasp,
            'START_EXPR_RECOG': lambda text: self.recognize('expression', text),
            'START_GESTURE_RECOG': lambda text: self.recognize('gesture', text),
        }

    # ------------------------------------------------------------ 会话
    def current(self):
        """返回当前语音会话的 mode()；没有开启时返回 None。"""
        mode = self.client.mode()
        if not mode['armed'] or mode['kind'] != KIND:
            if self.session:
                self.log('语音会话已结束：' + str(mode.get('message') or '网页已关闭、次数用完或到期'))
            self.session = None
            return None
        if mode['session_id'] != self.session:
            self.session = mode['session_id']
            grasp = mode.get('grasp')
            self.log(f"语音会话 {self.session[:8]}：最多 {mode['max_actions']} 次；竖直=“{mode['poses']['vertical']}”，"
                     f"观察=“{mode['poses']['observe']}”；舵机用时 {mode['servo']['motion_ms']} ms；"
                     f"跳舞 {mode['routines']}；抓方块 " + (f"{grasp['task_mode']}" if grasp else '未开启'))
        return mode

    def handle(self, action, text):
        """执行一条已分类的命令；返回 True 表示动作已执行完成。开发板拒绝只记录原因，不重试。"""
        mode = self.current()
        if mode is None:
            self.log(f'没有开启的语音会话，不执行“{text}”；请先在网页 /modes 开启语音控制')
            return False
        if mode['busy']:
            self.log('机械臂正在执行其他动作，本句不执行')
            return False
        try:
            return self.handlers[action](text)
        except BoardError as exc:
            self.log('开发板未执行：' + str(exc))
        except ValueError as exc:
            self.log('本句不执行：' + str(exc))
        return False

    def _finish(self, result, what):
        job = self.client.wait_job(result['job']['id'])
        self.log(f"{what}：{job['status']} {job.get('message') or job.get('error') or ''}".rstrip())
        return job['status'] == 'completed'

    # ------------------------------------------------------------ 各命令
    def dance(self, routine, text):
        result = self.client.voice_dance(self.session, routine, text)
        self.log(f"跳 {routine}，约 {result['duration_ms'] / 1000:.1f} 秒")
        return self._finish(result, '跳舞结束')

    def pose(self, pose, text):
        result = self.client.voice_pose(self.session, pose, text)
        return self._finish(result, {'vertical': '回到竖直姿态', 'observe': '回到观察姿态'}[pose])

    def servo(self, text):
        joint, angle = parse_servo_command(text)
        self.log(f'{joint}号舵机 → {angle}°')
        result = self.client.voice_servo(self.session, joint, angle, text)
        return self._finish(result, f'{joint}号舵机')

    def grasp(self, text):
        if any(word in text for word in PLACE_WORDS):
            raise ValueError('语音指定放置位置尚未支持；放置点在网页开启会话时已选好')
        color = requested_color(text)
        if self.detector is None:
            raise ValueError('没有可用的方块识别器（检查 pc/lan/lan_config.json 的 block_detect_url）')
        mode = self.current()
        if mode is None or not mode.get('grasp'):
            raise ValueError('当前语音会话没有开启抓方块')
        detector = ColorFilter(self.detector, color) if color else self.detector
        request = self.client.voice_grasp(self.session, text)['request_id']
        self.log('开始识别夹取' + (f'（只找{color}方块）' if color else ''))
        status = grasp_attempt(self.client, detector, self.session, request, mode['grasp']['min_score'], self.log)
        return status == 'completed'

    def recognize(self, which, text):
        """开启一段表情/手势识别：识别确认后跳对应舞蹈一次，或到时结束。"""
        if self.recognizer is None or self.source is None:
            raise ValueError('没有可用的表情手势识别器（检查 pc/lan/lan_config.json 的 gesture_url）')
        name = '表情' if which == 'expression' else '手势'
        wanted = {k: v for k, v in self.mapping.items() if (k in EXPRESSION_LABELS) == (which == 'expression')}
        if not wanted:
            raise ValueError(f'--map 里没有{name}标签对应的舞蹈')
        self.log(f'开始{name}识别（最长 {self.recog_seconds:.0f} 秒）：' + '，'.join(f'{k}→{v}' for k, v in wanted.items()))
        debounce = Debouncer(self.stable, self.threshold, max_missed_frames=1)
        deadline = time.monotonic() + self.recog_seconds
        while time.monotonic() < deadline:
            image = self.source.read()
            try:
                detected = self.recognizer.recognize(image)
            except RemoteProtocolError as exc:
                raise StopProgram('识别服务回复不合协议，程序停止（不触发任何动作）：' + str(exc)) from None
            except RemoteRecognizeError as exc:
                self.log('识别服务暂时不可用，本帧按“未识别”处理：' + str(exc))
                debounce.reset()
                time.sleep(1)
                continue
            if detected is not None and detected.label not in wanted:
                detected = None          # 本次只认{name}标签
            event = debounce.feed(detected)
            if event is None:
                continue
            self.log(f'{name}识别确认：{event.label}（{event.confidence:.2f}）→ {wanted[event.label]}')
            result = self.client.voice_dance(self.session, wanted[event.label], text, trigger=which,
                                             label=event.label, confidence=event.confidence)
            return self._finish(result, '跳舞结束')
        self.log(f'{name}识别结束：{self.recog_seconds:.0f} 秒内没有确认到约定的{name}')
        return False


def utterances(args, log):
    """依次产生 (描述, wav 路径或文字)。"""
    if args.wav:
        for path in args.wav:
            yield str(path), path
        return
    if args.text:
        while True:
            try:
                line = input('输入命令文字（直接回车退出）：').strip()
            except EOFError:
                return
            if not line:
                return
            yield '文字', line
        return
    from pc.voice_control.microphone import record_to_wav
    with tempfile.TemporaryDirectory(prefix='voice_mode_') as folder:
        while True:
            yield '麦克风', record_to_wav(Path(folder) / 'recording.wav')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--wav', type=Path, nargs='+', help='依次处理这些 WAV 文件后退出')
    source.add_argument('--text', action='store_true', help='跳过语音识别，直接输入文字')
    parser.add_argument('--config', default=str(lan_config.PATH), help='局域网地址配置文件（默认 pc/lan/lan_config.json）')
    parser.add_argument('--board', help='开发板地址（默认取配置文件 board）')
    parser.add_argument('--local', action='store_true', help='不用局域网识别服务，改用本机 --detector / --recognizer')
    parser.add_argument('--detector', default='block_vision.color_detector:ColorBlockDetector', help='本机方块检测器 模块:类名')
    parser.add_argument('--recognizer', default='gesture.recognizer:DanceRecognizer', help='本机表情手势识别器 模块:类名')
    parser.add_argument('--source', default='board', help='表情手势画面来源：board（开发板摄像头）/ webcam / webcam1')
    parser.add_argument('--map', default='smile=COURSE_DANCE_ROUTINE,palm=MUSIC_DANCE_ROUTINE', help='标签=动作,标签=动作')
    parser.add_argument('--recog-seconds', type=float, default=20, help='“开始表情/手势识别”后最长识别多少秒')
    parser.add_argument('--stable', type=int, default=3, help='表情手势有效识别次数')
    parser.add_argument('--threshold', type=float, default=0.2, help='表情手势置信度下限')
    parser.add_argument('--device', default='cpu', choices=('cpu', 'mps'), help='WeNet 推理设备（CPU 一句约 0.4 秒）')
    args = parser.parse_args()
    for path in args.wav or ():
        if not path.expanduser().is_file():
            parser.error(f'WAV 文件不存在：{path}')

    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    config = lan_config.load(args.config)
    board = args.board or config['board']
    client = BoardClient(board)
    log(f'配置：{args.config}；开发板 {board}')
    log('开发板：' + str(client.health()))

    from pc.voice_control.text_classification.classifier import IntentClassifier
    from pc.voice_control.text_classification.commands import NO_COMMAND, load_commands
    classifier, commands = IntentClassifier(), load_commands()
    asr = None
    if not args.text:
        from pc.voice_control.speech_recognition.wenet_asr import WeNetASR
        log('加载语音识别模型……')
        asr = WeNetASR(device=args.device)

    detect_url = '' if args.local else config['block_detect_url']
    gesture_url = '' if args.local else config['gesture_url']
    detector = RemoteDetector(detect_url, log=log) if detect_url else load_class(args.detector)
    recognizer = RemoteRecognizer(gesture_url, log=log) if gesture_url else None
    for name, service, error in (('方块识别', detector if detect_url else None, RemoteDetectError),
                                 ('表情手势识别', recognizer, (RemoteRecognizeError, RemoteProtocolError))):
        if service is not None:
            try:
                log(f'{name}服务：' + str(service.health()))
            except error as exc:
                log(f'{name}服务暂时连不上（说到相关命令时才会用到）：{exc}')
    log('方块识别：' + (detect_url or '本机 ' + args.detector) + '；表情手势识别：' + (gesture_url or '本机 ' + args.recognizer + '（首次使用时加载）'))

    voice = VoiceMode(client, log, detector=detector, recognizer=recognizer, mapping=parse_map(args.map),
                      recog_seconds=args.recog_seconds, stable=args.stable, threshold=args.threshold)
    if voice.recognizer is None:
        voice.recognizer = _Lazy(lambda: load_class(args.recognizer))
    voice.source = _Lazy(lambda: open_source(args.source, client))
    client.wait_for_mode(KIND, log=log)
    voice.current()
    try:
        for origin, item in utterances(args, log):
            text = item if args.text else asr.transcribe(item)
            log(f'[{origin}] 识别文字：{text or "（空）"}')
            if not text or not text.strip():
                log('未识别到指令')
                continue
            prediction = classifier.predict(text)
            if prediction.action in (None, NO_COMMAND):
                log('未识别到指令' + (f'（分数 {prediction.score:.2f}）' if prediction.score is not None else ''))
                continue
            log(f'命令：{prediction.action}（{commands[prediction.action]}，分数 {prediction.score:.2f}）')
            voice.handle(prediction.action, text)
    except StopProgram as exc:
        log(str(exc))
    except KeyboardInterrupt:
        log('Ctrl+C：请求结束会话（已下发的动作无法撤回）')
        try:
            client.stop()
        except BoardError as exc:
            log(str(exc))
    finally:
        for thing in (voice.source, voice.recognizer, detector):
            close = getattr(thing, 'close', None)
            if close:
                close()


class _Lazy:
    """第一次用到时才创建（避免没说识别命令时就加载本机模型或打开摄像头）。"""

    def __init__(self, factory):
        self.factory, self.value = factory, None

    def __getattr__(self, name):
        if name in ('factory', 'value'):
            raise AttributeError(name)
        if name == 'close':
            return (lambda: self.value.close()) if self.value is not None and hasattr(self.value, 'close') else None
        if self.value is None:
            self.value = self.factory()
        return getattr(self.value, name)


if __name__ == '__main__':
    main()
