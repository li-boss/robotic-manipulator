# WAV 语音识别

使用本地 WenetSpeech U2++ Conformer 检查点，通过 PyTorch 将 WAV 中的语音转成文字，主要面向普通话。当前是文件转写，不包含语种检测、麦克风录音、标点恢复或机械臂控制。

**当前状态：代码已编写，尚未安装依赖、加载权重、执行推理或运行测试。CPU、MPS、CUDA 的实际兼容性及识别效果均待验证。**

## 目录

```text
speech_recognition/
├── __init__.py
├── audio.py                   # WAV、重采样、Fbank、序列补齐
├── wenet_asr.py               # 模型加载、推理、CTC 解码、词表映射
├── demo.py                    # 命令行入口
├── requirements.txt
├── build-constraints.txt      # 旧版依赖的构建工具约束
├── README.md
├── models/wenetspeech/        # 本地模型资源，已被 Git 忽略
│   ├── final.pt
│   ├── train.yaml
│   ├── units.txt
│   └── global_cmvn
└── data/                     # 本地音频，已被 Git 忽略
    └── sample.wav
```

四个模型文件已从工作区的 `20220506_u2pp_conformer_exp_wenetspeech/` 搬入 `models/wenetspeech/`。原目录里的 `dev_*`、`test_*` 是官方评测结果，仍保留在原处。样例音频从工作区的 `sample.wav` 复制到 `data/`。

模型来自 [WeNet 官方预训练模型列表](https://github.com/wenet-e2e/wenet/blob/main/docs/pretrained_models.md)中的 WenetSpeech / Checkpoint Model。资源必须整套使用；课程 `.om` 的词表不能替代此处的 `units.txt`。模型遵循对应数据集许可，详见官方下载说明。

模型目录相对于模块文件定位，与启动程序时的工作目录无关；也可通过 `--model-dir` 指定其他位置。`train.yaml` 中的旧 `exp/...` 路径在加载时被替换为该目录下的资源路径，仅修改内存配置，不改原文件。

## 安装环境（后续执行）

本模块使用 **Python 3.11.17**，安装命令显式指定该版本；已在本机 uv 的 macOS ARM64 可下载列表中确认存在。当前组合为 WeNet `v3.1.0`、PyTorch/torchaudio `2.2.2`、NumPy `1.26.4`。Python 3.11 适合这套旧版推理依赖；它并非 WeNet 官方唯一指定的版本，当前组合也尚未完成实际安装和推理验证。

下面命令均在项目根目录执行。为避免覆盖之前的 Python 3.12 环境，语音模块使用独立的 `speech_recognition/.venv`：

```shell
uv python install 3.11.17
uv venv --python 3.11.17 speech_recognition/.venv
source speech_recognition/.venv/bin/activate
uv pip install --python speech_recognition/.venv/bin/python \
  -r speech_recognition/requirements.txt \
  --build-constraint speech_recognition/build-constraints.txt
```

WeNet 固定为官方 `v3.1.0`，代码使用该版本的 `wenet.transformer` 模块路径。依赖中的 Git 地址需要系统已安装 Git。PyTorch 与 torchaudio `2.2.2` 提供 Python 3.11 的 macOS ARM64 安装包。PyTorch 2.2.2 源码仍包含 WeNet v3.1.0 导入的 `Union` 等符号。

之前的 `cannot import name 'Union' from torch.nn.modules.conv` 来自旧 WeNet 对新版 PyTorch 内部接口的依赖，不是 Python 3.12 语法问题；只切换 Python、却继续安装最新 PyTorch，仍可能报错。修改 `.python-version` 也不会更换已有虚拟环境中的解释器，需使用上面的新环境。

`requirements.txt` 固定直接依赖及关键数值计算依赖，不是所有传递依赖的完整锁文件。`build-constraints.txt` 单独约束构建隔离环境中的 setuptools/wheel，因为 Whisper `20231117` 的安装脚本依赖 `pkg_resources`；仅在运行依赖中固定 setuptools 不能约束隔离构建。

安装后检查关键版本：

```shell
python --version
python -c "import torch, torchaudio; print(torch.__version__, torchaudio.__version__)"
```

期望输出 Python `3.11.17`，并且 `torch`、`torchaudio` 都是 `2.2.2`。

NVIDIA 机器应按照 [PyTorch 历史版本安装页面](https://pytorch.org/get-started/previous-versions/)选择 `2.2.2` 对应的 CUDA 安装命令，再安装本模块依赖；不要单独升级 torch 或 torchaudio。Mac 无需安装 CUDA。Whisper 等包属于上游安装依赖，本模块只加载本地 WenetSpeech 权重，不主动下载其他模型。

版本依据：[WeNet v3.1.0 依赖](https://github.com/wenet-e2e/wenet/blob/v3.1.0/requirements.txt)、[PyTorch 2.2.2 卷积模块](https://github.com/pytorch/pytorch/blob/v2.2.2/torch/nn/modules/conv.py)、[Whisper 20231117 安装脚本](https://github.com/openai/whisper/blob/v20231117/setup.py)。

## 命令行使用

从项目根目录识别样例，先选择 CPU：

```shell
python -m speech_recognition.demo speech_recognition/data/sample.wav --device cpu
```

识别自己的 WAV，路径包含空格时加引号：

```shell
python -m speech_recognition.demo "/path/to/my recording.wav" --device cpu
```

在 Mac 上尝试 MPS，在 NVIDIA 机器上选择 CUDA：

```shell
python -m speech_recognition.demo speech_recognition/data/sample.wav --device mps
python -m speech_recognition.demo speech_recognition/data/sample.wav --device cuda
```

可省略 `--device` 使用默认的 `auto`，依次检查 CUDA、MPS、CPU。自动选择只检查设备可用性，不保证模型所有算子都兼容；显式请求不可用设备会报错，推理失败时也不会自动换设备重跑。MPS 出现算子兼容问题时，可手动使用 `--device cpu`。

使用另一个目录下的同类模型资源：

```shell
python -m speech_recognition.demo /path/to/audio.wav \
  --model-dir /path/to/wenetspeech \
  --device cpu
```

程序将识别文字输出到标准输出，将设备信息和错误输出到标准错误；成功返回退出码 `0`，加载或识别失败返回 `1`，命令参数错误返回 `2`。没有识别到文字时输出空行并提示。本模块尚未产生实际识别结果，文档不提供预设成功输出。

## Python 接口

模型对象可重复使用，避免每次识别重新加载权重：

```python
from speech_recognition.wenet_asr import WeNetASR

recognizer = WeNetASR(device="cpu")
text = recognizer.transcribe("speech_recognition/data/sample.wav")
print(text)
```

`transcribe()` 接收 WAV 路径并返回字符串；`preprocess()` 返回补齐后的特征及真实长度，`post_process()` 对逐帧编号执行 CTC 合并，`map_batch()` 将合并后的编号映射为文字。

## 推理流程及课程对应

1. 使用 SoundFile 读取 WAV 为 float32，多声道取平均得到单声道
2. 按配置重采样至 16 kHz，在 CPU 上提取 80 维 Kaldi Fbank，帧长 25 ms、帧移 10 ms
3. 提取特征前将归一化浮点波形乘以 32768，与 WeNet 的 PCM16 幅度尺度对齐；推理时 `dither=0`，不使用训练配置中的速度扰动、SpecAugment 等增强
4. `pad_sequence()` 按批次最长长度补零，并保留真实长度；单文件无需固定长度截断，不沿用 `.om` 的固定输入长度限制
5. 从 `train.yaml` 构建官方 Conformer、双向 Transformer 解码器和 CTC 模块，用 `torch.load(..., weights_only=True)` 读取 `final.pt`；旧检查点若缺少编码器及左右解码器的三个固定正弦位置编码缓存（`pe`），使用模型按公式生成的缓存补齐，然后严格检查所有参数键和形状。已有的 `pe` 不会被覆盖，其他缺失或不匹配仍会报错
6. 在所选设备上以 FP32、`eval()`、`inference_mode()` 执行编码器和 CTC 输出层；全局 CMVN 在编码器中执行一次，不在外部重复归一化
7. 去除 padding 对应的输出帧，逐帧选取最大概率编号，先合并相邻重复项，再去掉 blank，最后按 `units.txt` 拼接文字，将 `▁` 还原为空格

完整网络用于严格加载检查点；第一版识别只执行编码器和 CTC 分支，不进行 attention rescoring。CTC 合并会保留由 blank 隔开的重复字，例如 `你、blank、你` 得到“你你”，不应合并成一个字。

当前代码针对 `conformer + bitransformer + char + global_cmvn + conv2d` 配置。少于 7 帧的音频会明确报错；不做音量归一化、去噪或静音检测，也不自动补标点。长音频按整段执行，耗时与内存占用会增加，目前未实现长音频分段。实际识别内容可能与课程原 `.om` 不同，因为两者并非已确认的同一检查点。

参考接口：[WeNet v3.1.0 模型构建](https://github.com/wenet-e2e/wenet/blob/v3.1.0/wenet/utils/init_model.py)、[编码器](https://github.com/wenet-e2e/wenet/blob/v3.1.0/wenet/transformer/encoder.py)、[音频预处理](https://github.com/wenet-e2e/wenet/blob/v3.1.0/wenet/dataset/processor.py)。
