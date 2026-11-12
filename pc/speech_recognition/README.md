# 语音识别

使用 WeNet v3.1.0 的 WenetSpeech U2++ Conformer 模型，将 WAV 转为普通话文字。

## 目录

- `audio.py`：读取音频、重采样、提取 Fbank。
- `wenet_asr.py`：加载模型并转写。
- `demo.py`：命令行入口。
- `models/wenet/`：`final.pt`、`train.yaml`、`units.txt`、`global_cmvn`。
- `data/sample.wav`：示例音频。

## 安装与运行

在项目根目录执行：

```shell
uv venv --python 3.11.17 pc/speech_recognition/.venv
uv pip install --python pc/speech_recognition/.venv/bin/python \
  -r pc/speech_recognition/requirements.txt \
  --build-constraint pc/speech_recognition/build-constraints.txt
pc/speech_recognition/.venv/bin/python -m pc.speech_recognition.demo \
  pc/speech_recognition/data/sample.wav --device cpu
```

Mac 当前使用 CPU；MPS 对示例音频会输出空文字。更换音频或模型时可用 `--model-dir` 和 `--device`。

## Python 接口

```python
from pc.speech_recognition.wenet_asr import WeNetASR

recognizer = WeNetASR(device="cpu")
text = recognizer.transcribe("pc/speech_recognition/data/sample.wav")
```