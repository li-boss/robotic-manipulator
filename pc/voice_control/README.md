# 语音控制

`speech_recognition/` 将 WAV 转为文字，`microphone.py` 录音，`commands.py` 从 `commands.json` 读取口令并匹配动作；`pc/run_voice_dance.py` 调用开发板接口。

在 `commands.json` 的 `circle`、`course` 列表中增删说法；下次运行即生效。例如“画圈”“画一个圈”都映射到 `circle`。只忽略空格和标点，整句不匹配就不提交动作；“不要画圈”等否定句不会触发。

## 安装

在项目根目录执行：

```shell
uv venv --python 3.11.17 pc/voice_control/.venv
uv pip install --python pc/voice_control/.venv/bin/python \
  -r pc/voice_control/speech_recognition/requirements.txt \
  -r pc/voice_control/requirements.txt \
  --build-constraint pc/voice_control/speech_recognition/build-constraints.txt
```

## 运行

先启动本地模拟服务并在 `http://127.0.0.1:8092/modes` 开启“表情手势跳舞”模式。模拟服务需要另行安装 `pc/requirements.txt` 中的 OpenCV。

```shell
pc/voice_control/.venv/bin/python -m pc.run_voice_dance --wav /path/to/command.wav
pc/voice_control/.venv/bin/python -m pc.run_voice_dance --mic
```

`--mic` 按回车开始、再按回车结束。默认连接本机模拟服务；实机需显式传入 `--board`，并由现场人员先开启对应会话。每段音频最多提交一次动作。
