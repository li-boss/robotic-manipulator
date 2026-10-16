# 智能机械臂垃圾分拣系统

本仓库目前仅建立四人并行开发的目录和空白文件骨架，尚未编写功能代码，也未完成设备联调或验收。

| 成员 | 主负责目录 | 主要内容 |
| --- | --- | --- |
| A | `camera_service/`、`waste_vision/`、`calibration_tools/samples/` | 相机、垃圾识别、标定采样 |
| B | `calibration_tools/solver/`、`robot_executor/` | 标定求解、机械臂动作与安全校验 |
| C | `contracts/`、`task_service/`、`event_service/`、`web_dashboard/` | 接口契约、任务调度、事件服务与轻量看板 |
| D | `emotion_interaction/`、`integration_tools/` | 笑脸互动、模拟器与联调工具 |

各模块先依据 `contracts/` 的接口约定，使用 `integration_tools/mocks/` 中的模拟依赖并行开发。空文件名仅表示建议的职责位置；接口、硬件参数和运行命令须经四人确认后再实现。
