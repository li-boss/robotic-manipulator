import argparse
from pathlib import Path
import sys
import time


def run_loop(recognizer, wav_file: Path, input_fn=input, output=print) -> None:
    """保持模型加载；每次按回车后重新转写同一个 WAV，输入 q 退出。"""
    while True:
        try:
            choice = input_fn("按回车识别一次，输入 q 退出：").strip().lower()
        except EOFError:
            return
        if choice in {"q", "quit"}:
            return
        if choice:
            output("请输入回车或 q")
            continue
        started = time.perf_counter()
        text = recognizer.transcribe(wav_file)
        elapsed = time.perf_counter() - started
        output(text or "（空）")
        output(f"本次转写耗时：{elapsed:.3f} 秒")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用本地 WeNet 模型将 WAV 语音转成文字"
    )
    parser.add_argument("wav", type=Path, help="输入 WAV 文件路径")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "models" / "wenet",
        help="包含 final.pt、train.yaml、units.txt、global_cmvn 的目录",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
        help="auto 优先 CUDA，否则 CPU；MPS 需显式指定",
    )
    parser.add_argument("--loop", action="store_true", help="保持模型加载，按回车反复转写同一 WAV")
    args = parser.parse_args()
    if not args.wav.expanduser().is_file():
        parser.error(f"音频文件不存在：{args.wav}")
    try:
        # 延迟导入，使查看命令帮助无需先安装推理依赖。
        from .wenet_asr import WeNetASR

        recognizer = WeNetASR(model_dir=args.model_dir, device=args.device)
        print(f"计算设备：{recognizer.device}", file=sys.stderr)
        if args.loop:
            run_loop(recognizer, args.wav)
        else:
            text = recognizer.transcribe(args.wav)
            print(text)
            if not text:
                print("模型未输出可显示文字。", file=sys.stderr)
    except KeyboardInterrupt:
        print("已退出循环。", file=sys.stderr)
        return 130
    except ImportError as exc:
        print(
            f"依赖导入失败：{exc}。请按 pc/voice_control/speech_recognition/README.md 安装环境。",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"识别失败：{exc}", file=sys.stderr)
        if args.device == "mps":
            print("如遇 MPS 算子兼容问题，可显式指定 --device cpu。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
