# 语音控制

`pc.voice_control.main` 是语音控制的正式入口。它把 WeNet 语音识别、字符 TF-IDF 与逻辑回归命令分类，以及八类命令的会话执行串在一起。动作处理和舵机文字解析都在 `actions.py`；`pipeline.py` 提供单句分类与分发函数。

## 运行

使用本模块原有的 Python 3.11.17 环境 `pc/voice_control/.venv`。首次安装在仓库根目录执行：

```shell
uv venv --python 3.11.17 pc/voice_control/.venv
uv pip install --python pc/voice_control/.venv/bin/python -r pc/voice_control/requirements.txt --build-constraint pc/voice_control/build-constraints.txt
pc/voice_control/.venv/bin/python -m pc.voice_control.text_classification.train
```

先在开发板网页 `/modes` 预览并开启“语音控制”会话，再运行 `pc/voice_control/.venv/bin/python -m pc.voice_control.main`。

按回车开始说，再按回车结束。也可用 `--wav a.wav b.wav` 依次处理录音，或用 `--text` 输入文字并跳过 WeNet；`--local` 使用本机视觉模型，`--board` 指定开发板地址。默认地址来自 `pc/lan/lan_config.json`。WeNet 权重放在 `pc/voice_control/speech_recognition/models/wenet/`；分类模型生成在 `pc/voice_control/text_classification/models/classifier.joblib`，两者都不提交 Git。分类模型需在运行环境中训练，加载时会核对 scikit-learn 版本。

## 命令与执行

`commands.json` 包含八类动作和 `NO_COMMAND`。分类为 `NO_COMMAND` 或低于阈值时不执行；每句话最多触发一次，不自动重试。

- 两套舞蹈：音乐舞蹈、课程舞蹈。
- 两种姿态：竖直、观察。
- 单关节绝对角度：从文字提取一个舵机编号和整数角度，开发板核对本会话范围。
- 方块抓取：可按单一颜色筛选；目的地由网页会话预选，不接受语音指定位置。冻结一帧后经 `pc/lan` 的远端或本机检测器识别，选择合格候选中得分最高的一块，交由开发板定位、规划和执行。
- 表情或手势识别：在限定时间内确认连续帧的标签，按映射触发一套舞蹈。

开发板只执行网页当次会话预览中允许的动作、参数和次数。关闭会话或 Ctrl+C 不能撤回已下发的动作。详见 [`机械臂操作准则.md`](../../机械臂操作准则.md)。

## 测试

以下测试使用假舵机和合成相机，不连接真实机械臂：

```shell
pc/voice_control/.venv/bin/python -m unittest discover -s pc/voice_control/tests -t .
```
