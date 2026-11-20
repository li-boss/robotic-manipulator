# 语音识别

使用 WeNet v3.1.0 的 WenetSpeech U2++ Conformer 模型，将 WAV 转为普通话文字。依赖安装见[语音控制 README](../README.md)。

- `audio.py`：音频预处理。
- `wenet_asr.py`：模型加载与转写。
- `demo.py`：独立转写入口。
- `models/wenet/`：`final.pt`、`train.yaml`、`units.txt`、`global_cmvn`。

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.speech_recognition.demo \
  pc/voice_control/speech_recognition/data/sample.wav --device cpu
```

加 `--loop` 后模型只加载一次；每次按回车重新转写同一个 WAV，输入 `q` 退出。输出包含每次转写的耗时，方便比较 CPU 与 MPS 的预热后速度。

```python
from pc.voice_control.speech_recognition.wenet_asr import WeNetASR

text = WeNetASR(device="cpu").transcribe("audio.wav")
```

语音控制主入口默认使用 CPU，可通过 `--device mps` 切换；独立 demo 也可用 `--device cpu` 或 `--device mps`。当前 WeNet/PyTorch 组合已针对无填充帧时的 MPS 遮罩异常做局部修正。
