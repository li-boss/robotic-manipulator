# 机械臂视觉互动项目

当前项目以 `mechanical_arm` 中的 JJT 实现为机械臂标准，D 侧识别直接调用 JJT 现有 HTTP 接口。

| 目录 | 内容 |
| --- | --- |
| `mechanical_arm/` | 机械臂网页、相机、标定、规划、控制与测试 |
| `emotion_interaction/` | 笑脸、手势和联合识别；笑脸可直接调用 JJT |
| `integration_tools/` | 模型下载、模型检查和 D 侧测试入口 |
| `docs/` | D 侧实现与直接联调说明 |

## 直接联调

JJT 服务保持原接口：

- `GET /control/status`：读取模式、忙碌状态、CSRF 和已保存六关节姿态；
- `POST /control/states/execute`：执行一个经过页面保存的六关节姿态。

D 侧不自行定义机械臂动作参数。操作者先在 JJT 网页保存和核对有限姿态，再明确给出动作序列：

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo `
  --robot-url http://192.168.137.100:8080 `
  --action WAVE `
  --states "wave_left,wave_right,wave_left,wave_home"
```

笑脸确认后，程序显示完整计划并要求输入本次 `EXECUTE <request_id>`。只有输入完全一致才逐步调用 JJT；异常时停止后续步骤且不重发。未提供 `--robot-url` 和 `--states` 时仅本地识别。

机械臂启动方式和实机限制见 [mechanical_arm/README.md](mechanical_arm/README.md)，识别环境和模型见 [emotion_interaction/README.md](emotion_interaction/README.md)。
