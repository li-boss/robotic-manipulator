# 语音控制模式

把语音同学的语音识别（WeNet）和命令分类（`pc/voice_control/`）接到开发板的“语音控制”模式会话上。本目录只放接入代码，不改动 `pc/voice_control/` 的识别与分类逻辑。

## 能说的命令

即 `pc/voice_control/text_classification/commands.json` 的八类：

| 命令 | 例句 | 开发板执行 |
|---|---|---|
| `MUSIC_DANCE_ROUTINE` / `COURSE_DANCE_ROUTINE` | 跳音乐舞蹈 / 跳一段课程舞蹈 | 先在 300 ms 内回到竖直，再跳该套舞蹈 |
| `POSE_VERTICAL` / `POSE_OBSERVE` | 回到竖直姿态 / 回到观察姿态 | 开启会话时选定的姿态按钮（逐关节） |
| `SET_SERVO_ABS_ANGLE` | 把三号舵机转到一百二十度 | 只转这一个舵机；角度须为整数且在配置范围内，超出拒绝不截断；用时为开启会话时填写的值 |
| `START_BLOCK_GRASP` | 抓方块 / 抓黄色方块 | 一次完整的冻结 → 局域网识别 → 规划 → 执行；说颜色只抓该颜色 |
| `START_EXPR_RECOG` / `START_GESTURE_RECOG` | 开始表情识别 / 开始手势识别 | 在 `--recog-seconds`（默认 20 秒）内识别，确认后跳 `--map` 对应的舞蹈一次 |

分类为 `NO_COMMAND` 或分数低于阈值的话不触发任何动作。舵机编号、角度听不清或一句话里有多个时不提交。

## 环境（与其他电脑端程序共用 `.venv-mac`，Python 3.9）

```bash
uv pip install --python .venv-mac/bin/python -r pc/voice_control/requirements.txt --override pc/voice_mode/py39-overrides.txt --build-constraint pc/voice_control/build-constraints.txt
```

`py39-overrides.txt` 只把 scikit-learn、joblib 换成支持 3.9 的版本，其余版本照语音同学的 requirements.txt（torch 2.2.2、numpy 1.26.4 等）。

模型文件（都不入 Git）：

- WeNet 权重：`pc/voice_control/speech_recognition/models/wenet/`（`final.pt`、`train.yaml`、`units.txt`、`global_cmvn`）。
- 分类模型：用本环境训练生成 `pc/voice_control/text_classification/models/classifier.joblib`（分类器会核对 scikit-learn 版本，别的环境训练的文件不能用）：

```bash
.venv-mac/bin/python -m pc.voice_control.text_classification.train
```

## 运行

1. 网页 `/modes` → “语音控制”：选竖直姿态、观察姿态、舵机用时、是否允许抓方块（放置点、置信度下限）、最多次数和有效期，预览、勾选确认、开启。
2. Mac 上运行（识别服务地址取自 `pc/lan/lan_config.json`）：

```bash
.venv-mac/bin/python pc/voice_mode/run_voice_mode.py
```

按回车开始说，再按回车结束；每句话最多触发一次动作。其他输入方式：`--wav a.wav b.wav`（依次处理录音文件）、`--text`（直接打字，跳过语音识别）。`--local` 改用本机的方块检测器和表情手势识别器。WeNet 默认 CPU 推理（一句约 0.4 秒），`--device mps` 可选。

Ctrl+C 只停止本程序并请求结束会话，已下发的动作无法撤回，不是急停。

## 测试（假舵机 + 合成相机，不连接机械臂）

```bash
cd pc && ../.venv-mac/bin/python -m unittest discover -s voice_mode/tests -t .
```

开发板端的会话与接口测试在 `board/tests/test_voice_mode.py`。
