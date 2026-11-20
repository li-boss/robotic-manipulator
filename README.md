# 智能机械臂（DOFBOT 六自由度）

机械臂通过网线连接电脑：**开发板**负责摄像头、定位规划和舵机控制；**电脑**负责运行方块识别、表情手势识别和语音控制模型，把识别结果发给开发板。

| 功能 | 入口 | 状态 |
|---|---|---|
| 单关节调整、六关节姿态按钮 | 网页首页 `/` | 在用 |
| 人工框选方块 → 抓取/抓放 | 网页 `/manipulation` | 在用（随臂冻结定位、逆解、碰撞检查） |
| 相机内参 / 手眼标定 | 网页 `/calibration` | 在用 |
| 方块识别自动抓取 | 网页 `/modes` 开启 + `pc/block_vision/run_block_grasp.py` | 接口完成，示例颜色检测器；等待方块识别模型 |
| 表情手势跳舞 | 网页 `/modes` 开启 + `pc/gesture/run_gesture_dance.py` | 电脑端手势/微笑模型接入；默认读取开发板摄像头 |
| 跳舞预设按钮（音乐舞蹈 / 课程舞蹈） | 网页首页“跳舞预设” | 在用 |
| 电脑端规划提速 | `pc/block_vision/plan_server.py`（开发板自动使用并复核，没开则本地计算） | 完成；Windows 待实测 |
| 语音控制模式 | 网页 `/modes` “语音控制”开启 + `python -m pc.voice_control.main` | 八类语音命令（两套舞蹈、竖直/观察姿态、N号舵机转到X度、抓方块、开始表情/手势识别）全部接入；语音识别与分类模型来自 `pc/voice_control/`，见 [语音控制说明](pc/voice_control/README.md) |

## 目录

```
board/     开发板端：网页服务、控制层（arm.py）、抓放规划、模式会话、网页、模型、配置模板、测试、simulate.py
pc/        电脑端：armlink 通信与取帧、block_vision 抓取与规划、gesture 表情手势、lan 局域网识别服务与地址配置、
           voice_control 语音识别、分类与语音控制模式；测试分别位于各包的 tests/
docs/      文档（只保留在本地，不上传 GitHub）
tools/     run_tests.sh、Mac 启动器、设备数据迁移
机械臂课程资料/   课程原始资料（只读参考，体积大，不入 git）
```

## 先读

- 方块识别同学：[docs/给方块识别同学.md](docs/给方块识别同学.md)（接口、放哪里、怎么联调和上实机，自成一体）。
- 表情手势同学：[docs/给表情手势同学.md](docs/给表情手势同学.md)。

- 队友：[docs/接口与合并指南.md](docs/接口与合并指南.md) —— 怎么写检测器/识别器、怎么联调、怎么合并。
- 机械臂控制：[docs/开发板部署与启动.md](docs/开发板部署与启动.md)、[docs/抓放系统实现与算法说明.md](docs/抓放系统实现与算法说明.md)、[docs/相机标定使用说明.md](docs/相机标定使用说明.md)。
- 模型安装与启动（两套环境、权重获取、启动命令、故障处置）：[docs/模型安装与启动.md](docs/模型安装与启动.md)。
- 所有人：[AGENTS.md](AGENTS.md) 与 [机械臂操作准则.md](机械臂操作准则.md) —— 运动授权与安全规则，代码里强制执行。

## 快速开始（无硬件）

```bash
python board/simulate.py
```

浏览器打开 <http://127.0.0.1:8092/modes> 开启模式，然后：

```bash
python pc/block_vision/run_block_grasp.py --board http://127.0.0.1:8092 --once
```

板端与电脑视觉包测试（假设备，不连机械臂）：

```bash
zsh tools/run_tests.sh
```

语音控制模式使用独立的 Python 3.11.17 环境 `pc/voice_control/.venv`（安装见 [pc/voice_control/README.md](pc/voice_control/README.md)）。在 `/modes` 开启“语音控制”后：

```bash
pc/voice_control/.venv/bin/python -m pc.voice_control.main --board http://127.0.0.1:8092 --text
```

## 实机

```bash
python -B tools/mac/service.py --start --real
```

或双击 `启动机械臂网页.command`（启动开发板网页后，同一窗口接着运行本机电脑端规划，保持窗口打开即可）。网页已在运行时，只需双击 `启动电脑端规划.command`。打开 <http://192.168.137.100:8080>。启动本身不运动；运动只在网页上当次确认（或开启模式）后发生。

重构前的完整项目（含历史部署脚本、实施报告与证据）备份在 `../mechanicalArm_backup_before_refactor_20261106.tar.gz`。
