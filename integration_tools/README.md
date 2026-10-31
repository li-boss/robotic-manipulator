# D 侧工具

本目录只保留模型资源准备、模型检查和测试入口，不再模拟 A/B/C 服务，也不保留机器人中间层。

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
.venv\Scripts\python.exe -m integration_tools.check_gesture_model
.venv\Scripts\python.exe -m integration_tools.check_smile_model
.venv\Scripts\python.exe -m integration_tools.contract_tests
```

JJT 直连实现在 `emotion_interaction/interaction_request.py`。测试使用本机临时 HTTP 服务复现 JJT 的 `/control/status` 和 `/control/states/execute` 请求格式，不连接真实设备。
