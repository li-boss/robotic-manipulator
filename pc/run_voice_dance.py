"""把一段 WAV 或一次按键录音识别为固定口令，触发已授权的跳舞动作。"""

import argparse
from pathlib import Path
import tempfile

from pc.armlink import BoardClient, BoardError
from pc.voice_control.commands import parse_command


DEFAULT_SIMULATOR = "http://127.0.0.1:8092"


def run_once(wav_file, recognizer, client, log=print) -> bool:
    """每段音频最多提交一次动作；未匹配到口令时不访问开发板。"""
    text = recognizer.transcribe(wav_file)
    log(f"识别文字：{text or '（空）'}")
    routine = parse_command(text)
    if routine is None:
        log("未识别到指令")
        return False
    log(f"匹配动作：{routine}")

    mode = client.mode()
    if not mode.get("armed") or mode.get("kind") != "gesture_dance":
        raise BoardError("请先在开发板 /modes 页面预览并开启表情手势跳舞模式")
    if routine not in mode.get("routines", ()):
        raise BoardError(f"本次会话未授权动作：{routine}")
    if mode.get("busy"):
        raise BoardError("开发板正在执行其他任务，本次语音不提交动作")
    session_id = mode.get("session_id")
    if not session_id:
        raise BoardError("当前模式缺少会话编号，本次语音不提交动作")

    result = client.dance(session_id, routine, label=text)
    job = client.wait_job(result["job"]["id"])
    status = job.get("status")
    log(f"动作结果：{status} {job.get('message') or job.get('error') or ''}".rstrip())
    if status != "completed":
        raise BoardError(f"动作未完成：{status}；不会自动重试")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", type=Path, help="单个 WAV 文件")
    source.add_argument("--mic", action="store_true", help="按回车开始和结束一次麦克风录音")
    parser.add_argument(
        "--board", default=DEFAULT_SIMULATOR,
        help=f"开发板服务地址；默认仅连接本机模拟服务 {DEFAULT_SIMULATOR}",
    )
    parser.add_argument("--model-dir", type=Path, help="WeNet 模型目录，默认使用语音模块内的 models/wenet")
    args = parser.parse_args(argv)
    if args.wav and not args.wav.expanduser().is_file():
        parser.error(f"WAV 文件不存在：{args.wav}")

    try:
        from pc.voice_control.speech_recognition.wenet_asr import DEFAULT_MODEL_DIR, WeNetASR

        recognizer = WeNetASR(
            model_dir=args.model_dir or DEFAULT_MODEL_DIR, device="cpu"
        )
        client = BoardClient(args.board)
        if args.mic:
            from pc.voice_control.microphone import record_to_wav

            with tempfile.TemporaryDirectory(prefix="voice_dance_") as folder:
                wav_file = record_to_wav(Path(folder) / "recording.wav")
                run_once(wav_file, recognizer, client)
        else:
            run_once(args.wav, recognizer, client)
    except (BoardError, OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
        parser.exit(1, f"语音控制失败：{exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "已取消；已下发的机械臂动作无法撤回。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
