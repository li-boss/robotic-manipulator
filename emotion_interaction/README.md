# 笑脸与手势识别

本目录提供笑脸、手势和联合识别。笑脸可直接调用 JJT 已有网页接口；手势当前只显示本地候选，不控制机械臂。

## 环境

已按 Python 3.13、CPU 推理整理依赖：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r emotion_interaction/requirements.txt
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
```

模型保存在被 Git 忽略的 `emotion_interaction/models/`。下载工具固定来源并校验 SHA256。

## 本地识别

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo
.venv\Scripts\python.exe -m emotion_interaction.gesture_demo
.venv\Scripts\python.exe -m emotion_interaction.combined_demo
```

不传直连参数时不会访问 JJT。笑脸触发只记录到 `logs/`。

## 直接连接 JJT

先在 JJT 网页保存构成动作的六关节姿态。每个姿态必须有唯一名称，六个目标角度、执行顺序和用时均由 JJT 自身校验。

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo `
  --robot-url http://192.168.137.100:8080 `
  --action WAVE `
  --states "wave_left,wave_right,wave_left,wave_home"
```

联合识别使用相同参数：

```powershell
.venv\Scripts\python.exe -m emotion_interaction.combined_demo `
  --robot-url http://192.168.137.100:8080 `
  --action NOD `
  --states "nod_down,nod_up,nod_home"
```

启动时先执行只读的 `GET /control/status`。笑脸确认后：

1. 再读取 JJT 状态和保存姿态；
2. 打印实机/模拟模式及完整有限计划；
3. 要求输入本次 `EXECUTE <request_id>`；
4. 按顺序直接调用 `POST /control/states/execute`；
5. 每步查询 JJT job，完成后才进入下一步。

输入不匹配即取消。忙碌、姿态缺失、修订变化、模式变化、HTTP 异常或 job 失败都会停止后续步骤；程序不自动重发运动请求。

## 笑脸规则

- `smile_score = min(mouthSmileLeft, mouthSmileRight)`；
- 默认阈值 0.3；
- 连续 5 帧确认、3 帧释放、5000 ms 冷却；
- 帧最大年龄 500 ms；
- 无脸、多人、无效系数、重复或乱序帧不触发。

触发记录使用 32 位十六进制 `request_id`，与 JJT 请求号格式一致。它不包含机械臂角度；角度始终来自 JJT 已保存姿态。

## 测试

```powershell
.venv\Scripts\python.exe -m integration_tools.contract_tests
```

JJT 直连测试使用本机临时 HTTP 服务复现真实路径、Origin、CSRF、姿态修订和 job 状态，不连接机械臂。

模型效果、相机效果和实机动作仍须分别现场验收；软件单元测试通过不能替代路径安全确认。
