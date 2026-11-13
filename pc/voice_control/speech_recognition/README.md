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

```python
from pc.voice_control.speech_recognition.wenet_asr import WeNetASR

text = WeNetASR(device="cpu").transcribe("audio.wav")
```

Mac 当前使用 CPU；MPS 对示例音频会输出空文字。
