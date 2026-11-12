# 语音控制

`commands.py` 将转写文字映射为动作；`microphone.py` 录制单段 WAV。语音识别由 [speech_recognition](../speech_recognition/README.md) 提供，运行入口是 `pc/run_voice_dance.py`。

只接受“机械臂画圈”→ `circle` 和“机械臂跳课程舞”→ `course`。忽略空格、标点，其他文字不发送动作。

## 运行

先安装语音识别依赖，再安装录音依赖：

```shell
uv pip install --python pc/speech_recognition/.venv/bin/python -r pc/voice_control/requirements.txt
```

启动本地模拟服务（需另有 `pc/requirements.txt` 中的 OpenCV），在 `http://127.0.0.1:8092/modes` 预览并开启“表情手势跳舞”模式。然后运行：

```shell
pc/speech_recognition/.venv/bin/python -m pc.run_voice_dance --wav /path/to/command.wav
pc/speech_recognition/.venv/bin/python -m pc.run_voice_dance --mic
```

`--mic` 按回车开始、再按回车结束。默认连接本机模拟服务；实机需显式传入 `--board`，并由现场人员先在网页开启对应会话。每段音频最多提交一次动作。

## 接口

- `parse_command(text) -> str | None`：返回 `circle`、`course` 或 `None`。
- `record_to_wav(path) -> Path`：录制一次音频。
