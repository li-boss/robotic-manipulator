"""语音控制正式入口：python -m pc.voice_control.main。

先在开发板网页 /modes 预览并开启语音控制会话。每句话最多提交一次动作。
"""
import argparse
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from armlink import BoardClient, BoardError
from armlink.frames import load_class, open_source
from gesture.run_gesture_dance import parse_map
from lan import lan_config
from lan.remote_detector import RemoteDetectError, RemoteDetector
from lan.remote_recognizer import RemoteProtocolError, RemoteRecognizeError, RemoteRecognizer
from .actions import KIND, StopProgram, VoiceMode
from .pipeline import run_once


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
    with tempfile.TemporaryDirectory(prefix='voice_control_') as folder:
        while True:
            yield '麦克风', record_to_wav(Path(folder) / 'recording.wav')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--wav', type=Path, nargs='+', help='依次处理这些 WAV 文件后退出')
    source.add_argument('--text', action='store_true', help='跳过语音识别，直接输入文字')
    source.add_argument('--mic', action='store_true', help='按回车开始和结束麦克风录音（默认）')
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
    classifier = IntentClassifier()
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
            log(f'[{origin}]')
            run_once(item, asr, client, log, classifier=classifier, voice=voice, is_text=args.text)
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
