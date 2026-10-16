# 手势识别与互动（D）

当前优先实现手势，笑脸后置。摄像头入口、模型标签、连续帧确认、冷却和本地记录已实现。

## 环境准备

已在 Windows、Python 3.13.5 上验证。代码、依赖清单和下载脚本随 Git 提交；模型权重、虚拟环境和运行日志留在本地，队友需要按以下步骤准备。

在仓库根目录执行：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r emotion_interaction/requirements.txt
```

主要依赖已在 `requirements.txt` 固定版本：PyTorch 2.13.0、MediaPipe 0.10.35、NumPy 2.4.3、opencv-contrib-python 5.0.0.93。请使用独立环境，不添加 `--system-site-packages`；无需安装 TensorFlow，也无需额外安装 opencv-python。

## 模型下载

需要以下两个预训练模型：

| 模型 | 用途 | 来源 |
| --- | --- | --- |
| best_hand_gesture_classifier.pt | 识别 24 类手势 | [SirAlexiner/HandGestureRecognition](https://github.com/SirAlexiner/HandGestureRecognition) |
| hand_landmarker.task | 定位手部关键点 | [Google MediaPipe 官方模型](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) |

在仓库根目录执行，自动下载两个模型及上游配套示例，并校验权重 SHA256：

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
```

分类模型固定使用上游提交 `f13db616a66f521882fac2d6c02322854ebb1343`，下载位置：

```text
emotion_interaction/models/
├── HandGestureRecognition-main/
│   └── best_hand_gesture_classifier.pt
└── hand_landmarker.task
```

下载需要访问 GitHub 和 Google 模型下载服务。上游资源保持原样，保留 LICENCE.md（CC BY-NC-ND 4.0），来源与模型哈希详见 D 部分实施说明。下载脚本不会覆盖被手动修改的已有资源。

## 验证与运行

从仓库根目录运行（当前本机已创建独立 `.venv` 并安装依赖，不继承系统包）：

```powershell
.venv\Scripts\python.exe -m integration_tools.check_gesture_model
.venv\Scripts\python.exe -m integration_tools.contract_tests
.venv\Scripts\python.exe -m emotion_interaction.gesture_demo
```

窗口显示识别类别和 0～1 分数，按 Q 或 Esc 退出。摄像头不是 0 时加 `--camera 1`；回放视频加 `--video "视频路径"`。仅保存分类和时间日志到 `logs/`，不保存原始图像；视频回放的 captured_at 是本次读取时间，不能用于测原始采集延迟。

支持四类内部结果：fist、like、ok、palm；其他模型类别不会生成确认候选，多手输入也不触发。默认确认 5 个连续有效帧、阈值 0.8、冷却 5000 ms、帧有效期 500 ms、释放 3 帧。这些是开发初值；用户反馈现场初试正常，尚未量化准确率、误触发率或完成实机验收。

原方案 3.5 的互动接口只允许 `label=smile`，字段为 interaction_id、label、confidence、occurred_at、expires_at、requested_action；动作只允许 WAVE/NOD。本模块严格保留这些字段，不将 gesture 伪装成 smile。手势确认仅输出内部 LOCAL_CONFIRMED；真实发送适配器尚未实现，必须由 B/C 审核手势语义后接入。原笑脸请求可用 mock 验证，不能据此宣称手势已联动机械臂。

模型是 SirAlexiner/HandGestureRecognition 的未修改预训练权重，标签顺序来源于原仓库混淆矩阵及 ImageFolder 排序，绑定 SHA256；48 张原仓库示例验证一致，但不构成独立准确率评估。预处理输入是手部骨架图，不能直接把普通人脸/手部照片送入分类器。

`models/`、`.venv/` 和 `logs/` 按仓库规则忽略，不会随代码提交。仓库示例通过不代表现场准确率或真实机械臂联调已验收。

详见 [D 部分实施说明](../docs/D_emotion_integration.md)，含来源、限制、验证记录与交接事项。
