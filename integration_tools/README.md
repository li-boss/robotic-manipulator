# 模拟与联调工具（D）

已提供 D 侧离线测试和有限范围的 A/B/C 替身：

- fake_camera：独立帧元数据与二进制载荷、断流和过期帧。
- fake_detect：正常目标、低置信度、无目标和过期拒绝；当前只使用方案示例的“可回收物”，不擅自冻结四类类别表。
- fake_robot：仅原方案 3.5 的互动互锁与幂等，不实现分拣 action、关节控制或真实安全互锁。
- fake_event_sink：SQLite 提交后确认、断网、重启去重和 ID 冲突；不代替 B 的持久待发队列或 C 的业务实现。

在仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m integration_tools.contract_tests
.venv\Scripts\python.exe -m integration_tools.run_mock --scenario gesture
.venv\Scripts\python.exe -m integration_tools.run_mock --scenario robot_busy
.venv\Scripts\python.exe -m integration_tools.run_mock --scenario net_down
```

其他场景：normal、holding、fault、stopped、expired、duplicate、low_confidence、no_target、camera_down、expired_frame。
normal 是原笑脸协议的离线接单演练，不是完整分拣链路。gesture 为手势本地确认，随后按原协议限制阻止真实请求；没有偷偷改 label 或新增线上的字段。
日志在 logs/，打印契约版本、场景、ID、结果和路径。mock 返回 ACCEPTED 只表示接单，不能报告真实动作完成。

权重未纳入 Git（遵循现有 .gitignore）。其他机器先按 [模块 README](../emotion_interaction/README.md) 安装 Python 3.13、创建独立环境并安装依赖，然后在仓库根目录下载模型：

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
```

第一条下载两个手势相关模型，第二条下载笑脸模型，联合检测需要三个模型齐全。测试范围目前是 D 策略与协议样例，尚非七接口完整验收。
