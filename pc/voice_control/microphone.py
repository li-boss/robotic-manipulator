"""按回车开始、结束一次麦克风录音，供离线识别使用。"""

from pathlib import Path
from typing import Callable


def record_to_wav(path: str | Path, input_fn: Callable[[str], str] = input) -> Path:
    """用默认输入设备录单声道 WAV；音频采样率由设备决定。"""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("录音需要 sounddevice，请重新安装语音模块依赖") from exc
    import numpy as np
    import soundfile as sf

    try:
        device = sd.query_devices(kind="input")
        sample_rate = int(round(device["default_samplerate"]))
        if sample_rate <= 0:
            raise RuntimeError("麦克风未提供有效采样率")
        input_fn("按回车开始录音…")
        chunks = []
        stream_errors = []

        def callback(indata, _frames, _time, status):
            if status:
                stream_errors.append(str(status))
            chunks.append(indata.copy())

        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="float32", callback=callback
        ):
            input_fn("录音中；再按回车结束…")
    except EOFError as exc:
        raise RuntimeError("麦克风录音需要交互式终端") from exc
    except sd.PortAudioError as exc:
        raise RuntimeError(f"无法使用麦克风：{exc}") from exc

    if stream_errors:
        raise RuntimeError("录音过程中出现音频流错误：" + "; ".join(stream_errors))
    if not chunks:
        raise RuntimeError("没有录到音频")
    audio = np.concatenate(chunks, axis=0)
    destination = Path(path)
    sf.write(destination, audio, sample_rate, subtype="PCM_16")
    return destination
