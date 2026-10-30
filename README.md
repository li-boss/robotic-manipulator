# 智能机械臂垃圾分拣系统

本仓库从四人并行开发骨架开始建设。当前 D 部分已有可运行的手势、笑脸及联合检测入口，并提供部分离线模拟与测试工具；真实机械臂联调与整体验收尚未完成。各模块进度以对应目录说明为准。

| 成员 | 主负责目录 | 主要内容 |
| --- | --- | --- |
| A | `camera_service/`、`waste_vision/`、`calibration_tools/samples/` | 相机、垃圾识别、标定采样 |
| B | `calibration_tools/solver/`、`robot_executor/` | 标定求解、机械臂动作与安全校验 |
| C | `contracts/`、`task_service/`、`event_service/`、`web_dashboard/` | 接口契约、任务调度、事件服务与轻量看板 |
| D | `emotion_interaction/`、`integration_tools/` | 手势与笑脸互动、模拟器与联调工具 |

各模块先依据 `contracts/` 的接口约定，使用 `integration_tools/mocks/` 中的模拟依赖并行开发。空文件名仅表示建议的职责位置；接口、硬件参数和运行命令须经四人确认后再实现。

## D 部分安装与运行

请先阅读 [手势与笑脸模块 README](emotion_interaction/README.md)：其中列明 Windows 64 位 Python 3.13 环境、依赖安装、三个预训练模型的来源/下载命令/保存位置，以及独立和联合检测启动方法。模型、虚拟环境和日志不上传 Git，队友需要按说明在本机准备。

补充说明：[模拟与联调工具](integration_tools/README.md)、[D 部分实施记录](docs/D_emotion_integration.md)、[笑脸检测说明](docs/D_smile_detection.md)。当前摄像头演示仅本地识别/生成请求，不控制真实机械臂。
