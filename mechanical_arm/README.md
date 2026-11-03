# 机械臂控制与人工标注抓放（jjt）

本目录是 jjt 在本地开发的机械臂项目代码同步副本。设备端运行目录为 `/root/jjt`。

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md`、`机械臂操作准则.md` | 强制安全与运动授权规则，修改或运行脚本前必读 |
| `arm_playground/` | 统一控制层 `arm.py`（角度/用时校验、`Controller.write`）与关节测试 |
| `sorting_demo/` | 网页服务、人工标注抓放、标定、规划（含 ROS2 规划节点）及测试 |
| `sorting_demo/models/` | URDF、CAD 碰撞模型与网格（STL） |
| `camera_preview/` | 相机预览服务 |
| `mac_tools/` | Mac 端启动/状态检查与已审查源码指纹 |
| `deployment/` | 各次部署、仿真冒烟与标定资料生成脚本 |
| `output/pdf/` | ChArUco 标定纸 |
| `docs/` | 实现与算法说明、交接文档、需求审查文档 |
| `implementation_reports/` | 每次改动的实现与验证报告 |

启动方式见 `启动指令.md` 和 `Mac运行说明.md`。本地测试只使用模拟设备，不连接实机：

```bash
python -B -m unittest discover -s sorting_demo -p 'test_*.py'
```

实机运动须按 `AGENTS.md` 由用户在网页上当次确认；网页上的按钮仅为界面授权，标记“未验收”的项目未经过实机验证。

## D 笑脸互动接口适配

笑脸复用保存姿态的 `/control/states/execute`，附加带过期时间的 `interaction`。控制层在接单和每个关节下发前做最终检查；忙碌、夹物、故障、停止、状态未知/过旧或触发过期时拒绝/停止后续下发。实机默认缺少真实状态采样器，因此互动被拒绝，不能把 `busy=false` 当作无夹物或无故障。原网页手动控制规则保持原样。

新增只读 `GET /control/jobs/<request_id>` 查询历史，重启后不重新执行。直接姿态任务的 `completed` 表示指令下发流程结束，新增 `completion_kind=commands_sent`、`motion_verified=false`，不代表实机到位。

状态采样 callback、两端升级、缺少实测动作配置及离线验证说明见 [D/B 联调说明](../docs/D_emotion_integration.md)。本轮未连接或部署设备。
