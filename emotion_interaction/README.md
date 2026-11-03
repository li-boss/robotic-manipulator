# 笑脸与手势识别

本目录提供笑脸、手势和联合识别。笑脸可直接调用 JJT 已有网页接口；手势当前只显示本地候选，不控制机械臂。

本次接口适配已通过离线假控制器验证，未连接、部署或驱动真实机械臂。本地未找到 `mechanical_arm/arm_playground/manual_states.json`，没有可核验的 WAVE/NOD 姿态；仓库机械臂配置仍为未就绪、关节范围未填写。下文姿态名称均为示例，不代表设备上已经存在或已验证。

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
5. 按该步请求号查询 `GET /control/jobs/<request_id>`，指令下发流程结束后才进入下一步。

输入不匹配即取消。忙碌、姿态缺失、修订变化、模式变化、HTTP 异常或 job 失败都会停止后续步骤；程序不自动重发运动请求。

### 有效期、互锁与结果

- 触发记录新增 `expires_at`（Unix 毫秒）。默认有效期 30000 ms，包含终端确认等待和后续下发时间；可用 `--request-ttl-ms 60000` 调整，允许 1～120000 ms。电脑与设备需校准时钟。客户端准备/下发前、服务端接单及每个关节下发前均检查有效期；过期后不再下发，已发出的运动不能撤回，不自动复位或松爪。请按已验证动作时长留足预算，过期需重新识别和确认。
- 复用 `/control/states/execute`，新增 `interaction` 上下文。新客户端要求服务端报告 `interaction_api_version=1`；旧设备服务会收到明确升级提示，不能只更新电脑侧代码。
- 服务端检查忙碌，以及有来源、500 ms 内的新鲜夹物/故障/停止状态。实机默认没有该状态来源，返回 `DEVICE_STATE_UNKNOWN` 并拒绝笑脸动作；不会用舵机角度、`hardware_ready` 或 `busy=false` 冒充空载且无故障。
- B 需通过 `Demo(..., interaction_state_provider=...)` 或 `Control` 的同名参数接入真实状态采样函数。契约见 [联调说明](../docs/D_emotion_integration.md)。没有提供“强制安全”命令行开关；模拟模式明确标记 `source=simulation`，不作为实机证据。
- 原网页手动姿态执行保持原接口和确认流程；新增严格互锁针对带 `interaction` 的笑脸请求。
- 每步请求号固定绑定触发记录和姿态。重复调用先查历史，不再次 POST；重启后只有接单记录而无终态的任务返回 `unknown`，禁止自动重发。
- D 输出 `COMMANDS_SENT`、`motion_verified=false`，表示指令流程完成，不能称为实际到位或互动成功。断网/超时结果记为 `UNKNOWN`，日志保留该步请求号，供只读查询；不自动换 ID 重试。

## 笑脸规则

- `smile_score = min(mouthSmileLeft, mouthSmileRight)`；
- 默认阈值 0.3；
- 连续 5 帧确认、3 帧释放、5000 ms 冷却；
- 帧最大年龄 500 ms；
- 无脸、多人、无效系数、重复或乱序帧不触发。

触发记录使用 32 位十六进制 `request_id`，与 JJT 请求号格式一致，字段为 `request_id/action/confidence/detected_at/expires_at`。它不包含机械臂角度；角度始终来自 JJT 已保存姿态。这是合并后 HTTP 直连协议的扩展，不是原方案六字段协议。

## 测试

```powershell
.venv\Scripts\python.exe -m integration_tools.contract_tests
```

JJT 直连测试注入内存传输，交叉测试还直接调用 B 的真实 HTTP handler 和控制层，使用临时测试姿态及假硬件；不打开网络连接、不连接机械臂。覆盖过期、互锁、接单后状态变化、历史/重启去重、丢失响应及结果语义。测试姿态只存在于临时目录，不写入实际动作配置。

模型效果、相机效果和实机动作仍须分别现场验收；软件单元测试通过不能替代路径安全确认。
