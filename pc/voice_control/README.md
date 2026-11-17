# 语音控制

## 目录

```text
voice_control/
├── main.py                     # 命令行入口
├── pipeline.py                 # 识别、分类、会话检查与动作提交
├── microphone.py               # 按键录音
├── speech_recognition/         # WeNet 推理、音频处理与模型文件
├── text_classification/        # 转写文字的命令分类
│   ├── classifier.py           # TF-IDF、逻辑回归、模型加载与预测
│   ├── commands.py/json        # 命令类别及含义
│   ├── dataset.py              # 标注数据读取与校验
│   ├── train.py                # 训练并保存模型
│   ├── evaluate.py             # 独立数据评估
│   ├── data/                   # train/val/test.jsonl
│   └── models/                 # 训练生成的 classifier.joblib（不提交 Git）
├── tests/                      # 测试文件
├── requirements.txt            # 全部运行依赖
└── build-constraints.txt       # 安装时的构建依赖约束
```

## 快速开始

在项目根目录执行：

```shell
uv venv --python 3.11.17 pc/voice_control/.venv
uv pip install --python pc/voice_control/.venv/bin/python \
  -r pc/voice_control/requirements.txt \
  --build-constraint pc/voice_control/build-constraints.txt
```

运行前把 WeNet 模型文件放进 `speech_recognition/models/wenet/`，并按“文本分类”一节训练出 `classifier.joblib`。`main.py` 当前用 MPS 推理，`--board` 默认指向本机模拟服务 `http://127.0.0.1:8092`。

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.main --wav /path/to/command.wav
pc/voice_control/.venv/bin/python -m pc.voice_control.main --mic
```

`--mic` 按两次回车分别开始和结束录音

连接实机时显式指定 `--board`，并由现场人员先在网页开启会话。每段音频最多提交一次动作。

## 语言识别

`speech_recognition/` 使用 WeNet v3.1.0 的 WenetSpeech U2++ Conformer 模型。`audio.py` 读取 WAV 并提取声学特征，`wenet_asr.py` 加载 `models/wenet/` 中的权重、词表和配置，把音频转成文字。单独检查转写效果可以运行 `demo.py`：

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.speech_recognition.demo \
  pc/voice_control/speech_recognition/data/sample.wav --device cpu
```

`demo.py` 不调用分类器或开发板。加 `--loop` 后，模型只加载一次，每次按回车重新转写同一个 WAV；可以用 `--device mps` 比较本机推理速度。

## 文本分类

`commands.json` 定义八个动作类别和 `NO_COMMAND`。`data/` 中的 `train.jsonl`、`val.jsonl`、`test.jsonl` 分别用于训练、设置拒绝阈值和评估。否定句、询问句及无关内容都明确标为 `NO_COMMAND`；训练时不会临时生成样本。

`classifier.py` 用字符 TF-IDF 和逻辑回归预测命令。模型选中 `NO_COMMAND` 时原样返回，动作分数低于阈值时返回 `action=None`。`pipeline.py` 在提交动作前把 `NO_COMMAND` 当作无动作处理。模型文件放在 `text_classification/` 下的 `models/classifier.joblib`，不提交 Git；只加载自己训练生成的文件。

数据或分类代码有变化时，重新训练并评估：

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.text_classification.train
pc/voice_control/.venv/bin/python -m pc.voice_control.text_classification.evaluate
```

用当前模型交互式试一句话：

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.tests.test_text_classification
```

输入文字后会显示类别和分数，直接回车退出。若要替换分类器，可设置 `VOICE_CONTROL_CLASSIFIER=模块:类名`。
