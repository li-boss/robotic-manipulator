# 语音控制

## 目录

```text
voice_control/
├── main.py                     # 命令行入口
├── pipeline.py                 # 识别、分类、会话检查与动作提交
├── microphone.py               # 按键录音
├── speech_recognition/         # WeNet 推理、音频处理与模型文件
├── text_classification/        # 分类接口与临时口令实现
│   ├── classifier.py           # ActionPrediction、ActionClassifier、load_classifier
│   ├── rules.py                # 模型加入前的 RuleClassifier
│   ├── commands.py             # 整句口令解析
│   └── commands.json           # 临时口令表
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

先启动本地模拟服务，并在 `http://127.0.0.1:8092/modes` 开启跳舞模式。模拟服务还需要 OpenCV。

```shell
pc/voice_control/.venv/bin/python -m pc.voice_control.main --wav /path/to/command.wav
pc/voice_control/.venv/bin/python -m pc.voice_control.main --mic
```

`--mic` 按两次回车分别开始和结束录音。默认连接本机模拟服务；连接实机时显式指定 `--board`，并由现场人员先在网页开启会话。每段音频最多提交一次动作。
