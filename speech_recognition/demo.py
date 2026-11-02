"""用法：python -m speech_recognition.demo /path/to/audio.wav --device cpu。"""

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="使用本地 WeNet 模型将 WAV 语音转成文字")
    parser.add_argument("wav", type=Path, help="输入 WAV 文件路径")
    parser.add_argument("--model-dir", type=Path,
                        default=Path(__file__).resolve().parent / "models" / "wenetspeech",
                        help="包含 final.pt、train.yaml、units.txt、global_cmvn 的目录")
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto",
                        help="auto 按 CUDA → MPS → CPU 的顺序选择，默认 auto")
    args = parser.parse_args()
    if not args.wav.expanduser().is_file():
        parser.error(f"音频文件不存在：{args.wav}")
    try:
        # 延迟导入，使查看命令帮助无需先安装推理依赖。
        from .wenet_asr import WeNetASR

        recognizer = WeNetASR(model_dir=args.model_dir, device=args.device)
        print(f"计算设备：{recognizer.device}；解码：CTC greedy", file=sys.stderr)
        text = recognizer.transcribe(args.wav)
    except ImportError as exc:
        print(f"依赖导入失败：{exc}。请按 speech_recognition/README.md 安装环境。", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"识别失败：{exc}", file=sys.stderr)
        if args.device in {"mps", "auto"}:
            print("如遇 MPS 算子兼容问题，可显式指定 --device cpu。", file=sys.stderr)
        return 1
    print(text)
    if not text:
        print("模型未输出可显示文字。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
