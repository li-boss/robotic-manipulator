"""在 CPU 上读取 WAV、重采样并提取与 WeNet 训练一致的特征。"""

from pathlib import Path
from typing import Sequence

import soundfile as sf
import torch
import torchaudio
from torchaudio.compliance import kaldi


def load_wav(path: str | Path) -> tuple[torch.Tensor, int]:
    """返回 [1, samples] 的 float32 单声道波形和原始采样率。"""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在：{path}")
    with sf.SoundFile(str(path)) as audio:
        if audio.format not in {"WAV", "WAVEX", "RF64"}:
            raise ValueError("当前入口只接受 WAV 音频文件")
        sample_rate = audio.samplerate
        samples = audio.read(dtype="float32", always_2d=True)
    if samples.shape[0] == 0:
        raise ValueError("音频文件为空")
    waveform = torch.from_numpy(samples).mean(dim=1).unsqueeze(0)
    if not torch.isfinite(waveform).all():
        raise ValueError("音频包含 NaN 或无穷值")
    return waveform.contiguous(), sample_rate


def resample(
    waveform: torch.Tensor, sample_rate: int, target_rate: int
) -> torch.Tensor:
    """采样率不匹配时重采样；相同时直接返回。"""
    if sample_rate <= 0 or target_rate <= 0:
        raise ValueError("采样率必须为正整数")
    if sample_rate == target_rate:
        return waveform
    return torchaudio.functional.resample(waveform, sample_rate, target_rate)


def compute_fbank(
    waveform: torch.Tensor,
    sample_rate: int,
    *,
    num_mel_bins: int = 80,
    frame_length: float = 25.0,
    frame_shift: float = 10.0,
    window_type: str = "povey",
) -> torch.Tensor:
    """返回 [frames, mel_bins]；推理关闭 dither，不执行训练增强。"""
    if waveform.shape[-1] < int(sample_rate * frame_length / 1000):
        raise ValueError("音频太短，不足一个 Fbank 分析窗")
    # SoundFile 的浮点 PCM 约在 [-1, 1]，WeNet 的 Kaldi Fbank 使用 PCM16 尺度。
    return kaldi.fbank(
        waveform.cpu().float() * 32768.0,
        sample_frequency=float(sample_rate),
        num_mel_bins=num_mel_bins,
        frame_length=frame_length,
        frame_shift=frame_shift,
        window_type=window_type,
        dither=0.0,
        energy_floor=0.0,
    )


def pad_sequence(features: Sequence[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
    """按本批次最长序列补零，并保留真实长度；不截断音频内容。"""
    if not features or any(x.ndim != 2 or x.size(0) == 0 for x in features):
        raise ValueError("特征必须为非空的二维序列")
    lengths = torch.tensor([x.size(0) for x in features], dtype=torch.long)
    batch = torch.nn.utils.rnn.pad_sequence(
        list(features), batch_first=True, padding_value=0.0
    )
    return batch, lengths
