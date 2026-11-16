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
├── tests/                      # 本地模拟测试
├── requirements.txt            # 全部运行依赖
└── build-constraints.txt       # 安装时的构建依赖约束
```

## 安装与运行

在项目根目录执行：

```shell
uv venv --python 3.11.17 pc/voice_control/.venv
uv pip install --python pc/voice_control/.venv/bin/python \
  -r pc/voice_control/requirements.txt \
  --build-constraint pc/voice_control/build-constraints.txt
```

`commands.json` 定义 8 个命令类别。训练数据为 JSONL，每行包含 `text` 和 `action`；`action: null` 表示无命令。仓库中的数据是初始人工示例，实际语音转写需继续补充。先训练，再评估：

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.text_classification.train
pc/voice_control/.venv/bin/python -m pc.voice_control.text_classification.evaluate
```

训练使用字符 TF-IDF 和逻辑回归，从训练句生成否定/疑问样本，并用验证集设置拒绝阈值。推理直接加载 `models/classifier.joblib`；分类结果不确定时返回 `action=None`。训练产物只应加载自己生成的文件。若需替换分类器，可设置 `VOICE_CONTROL_CLASSIFIER=模块:类名`。

运行前先启动本地模拟服务，并在 `http://127.0.0.1:8092/modes` 开启跳舞模式。模拟服务还需要 OpenCV。

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.main --wav /path/to/command.wav
pc/voice_control/.venv/bin/python -m pc.voice_control.main --mic
```

`--mic` 按两次回车分别开始和结束录音。默认连接本机模拟服务；连接实机时显式指定 `--board`，并由现场人员先在网页开启会话。每段音频最多提交一次动作。目前只有两种跳舞命令接入执行流程；姿态、舵机和识别进程命令只完成分类，不会下发动作。当前数据与阈值尚未经真实 ASR 转写验证。
