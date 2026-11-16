"""语音控制命令行入口：python -m pc.voice_control.main。"""

import argparse
import os
from pathlib import Path
import tempfile

from pc.armlink import BoardClient, BoardError
from .pipeline import run_once
from .text_classification.classifier import load_classifier


DEFAULT_SIMULATOR = "http://127.0.0.1:8092"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", type=Path, help="单个 WAV 文件")
    source.add_argument("--mic", action="store_true", help="按回车开始和结束一次麦克风录音")
    parser.add_argument(
        "--board", default=DEFAULT_SIMULATOR,
        help=f"开发板服务地址；默认仅连接本机模拟服务 {DEFAULT_SIMULATOR}",
    )
    args = parser.parse_args(argv)
    if args.wav and not args.wav.expanduser().is_file():
        parser.error(f"WAV 文件不存在：{args.wav}")

    try:
        classifier_spec = os.environ.get("VOICE_CONTROL_CLASSIFIER")
        if not classifier_spec:
            raise RuntimeError("文本分类模型尚未接入；配置 VOICE_CONTROL_CLASSIFIER=模块:类名 后再运行")
        classifier = load_classifier(classifier_spec)
        from pc.voice_control.speech_recognition.wenet_asr import WeNetASR

        recognizer = WeNetASR(device="cpu")
        client = BoardClient(args.board)
        if args.mic:
            from pc.voice_control.microphone import record_to_wav

            with tempfile.TemporaryDirectory(prefix="voice_dance_") as folder:
                wav_file = record_to_wav(Path(folder) / "recording.wav")
                run_once(wav_file, recognizer, client, classifier=classifier)
        else:
            run_once(args.wav, recognizer, client, classifier=classifier)
    except (BoardError, OSError, ValueError, RuntimeError, ImportError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"语音控制失败：{exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "已取消；已下发的机械臂动作无法撤回。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
