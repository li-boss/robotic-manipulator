# 语音识别

使用 WeNet V3.1.0 的 WenetSpeech U2++ Conformer 模型，将 WAV 文件中的普通话转为文字。

## 目录结构

```text
pc/speech_recognition/
├── audio.py                 # 音频读取与特征提取
├── wenet_asr.py             # 模型加载与语音识别
├── demo.py                  # 命令行入口
├── requirements.txt         # 运行依赖
├── build-constraints.txt    # 安装时的构建依赖约束
├── models/wenet/            # 模型文件
│   ├── final.pt             # 训练好的模型参数
│   ├── train.yaml           # 网络结构和音频处理配置
│   ├── units.txt            # 词表
│   └── global_cmvn          # 特征归一化所需的数据
└── data/sample.wav          # 示例音频
```

## 快速开始

在项目根目录使用 Python 3.11.17 安装依赖：

```shell
uv python install 3.11.17
uv venv --python 3.11.17 pc/speech_recognition/.venv
source pc/speech_recognition/.venv/bin/activate
uv pip install -r pc/speech_recognition/requirements.txt \
  --build-constraint pc/speech_recognition/build-constraints.txt
```

识别示例或自己的 WAV 文件：

```shell
python -m pc.speech_recognition.demo pc/speech_recognition/data/sample.wav --device cpu
python -m pc.speech_recognition.demo /path/to/audio.wav --device cpu
```

`--device` 可选 `auto`（默认）、`cpu`、`mps` 或 `cuda`。`auto` 优先 CUDA，否则使用 CPU；当前模型在 Mac MPS 上对样例音频会输出空文本，手动选择 `mps` 仅用于排查。使用其他模型目录时，传入 `--model-dir /path/to/model`；该目录需包含上述四个模型文件。识别文字输出到标准输出。

## 对外接口

```python
from pc.speech_recognition.wenet_asr import WeNetASR

recognizer = WeNetASR(device="cpu")
text = recognizer.transcribe("pc/speech_recognition/data/sample.wav")
print(text)
```

- `WeNetASR(model_dir=..., device="auto")` 加载模型
- `transcribe(wav_file)` 接收 WAV 路径并返回识别文字
- 复用同一个 `recognizer` 可连续识别多个文件
