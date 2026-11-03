# D 侧工具

本目录只保留模型资源准备、模型检查和测试入口，不再模拟 A/B/C 服务，也不保留机器人中间层。

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
.venv\Scripts\python.exe -m integration_tools.check_gesture_model
.venv\Scripts\python.exe -m integration_tools.check_smile_model
.venv\Scripts\python.exe -m integration_tools.contract_tests
```

JJT 直连实现在 `emotion_interaction/interaction_request.py`。测试使用注入的内存传输及 B 端真实 HTTP handler/假控制器，校验 `/control/status`、`/control/jobs/<request_id>`、`/control/states/execute`；不打开网络连接或连接真实设备。新增互动协议要求两端同时更新，有效期、未知设备状态拒绝和实际到位未验证的说明见模块 README。
