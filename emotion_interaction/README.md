# 手势与笑脸识别互动（D）

已实现手势识别，并新增笑脸检测独立入口；两者共用现有独立 Python 环境。摄像头入口、连续帧确认、冷却和本地记录已实现，真实机械臂通信尚未接入。

## 环境准备

已在 Windows、Python 3.13.5 上验证。代码、依赖清单和下载脚本随 Git 提交；模型权重、虚拟环境和运行日志留在本地，队友需要按以下步骤准备。

首次使用先安装 **Windows 64 位 Python 3.13**（当前验证版本为 3.13.5），使用摄像头预览时需要可用的摄像头。用 `py -3.13 --version` 检查 Python 是否可用；如果提示找不到 py 或指定版本，先完成 Python 3.13 及启动器安装，再创建环境。当前使用 CPU 推理，无需为这些演示单独安装 CUDA，也无需先下载老师的数据集或重新训练。

在仓库根目录执行：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r emotion_interaction/requirements.txt
```

主要依赖已在 `requirements.txt` 固定版本：PyTorch 2.13.0、MediaPipe 0.10.35、NumPy 2.4.3、opencv-contrib-python 5.0.0.93。请使用独立环境，不添加 `--system-site-packages`；无需安装 TensorFlow，也无需额外安装 opencv-python。

## 模型下载

完整运行手势、笑脸和联合模式，需要以下 **三个** 预训练模型；只运行手势时使用前两个：

| 模型 | 用途 | 来源 |
| --- | --- | --- |
| best_hand_gesture_classifier.pt | 识别 24 类手势 | [SirAlexiner/HandGestureRecognition](https://github.com/SirAlexiner/HandGestureRecognition) |
| hand_landmarker.task | 定位手部关键点 | [Google MediaPipe 官方模型](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) |
| face_landmarker.task | 人脸定位和微笑动作系数 | [Google MediaPipe 官方人脸模型](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task) |

在安装依赖后，从仓库根目录依次执行，自动下载模型并校验权重 SHA256。第一条下载两个手势模型及上游配套示例，第二条下载笑脸模型：

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
```

分类模型固定使用上游提交 `f13db616a66f521882fac2d6c02322854ebb1343`，下载位置：

```text
emotion_interaction/models/
├── HandGestureRecognition-main/
│   └── best_hand_gesture_classifier.pt
├── hand_landmarker.task
└── face_landmarker.task
```

下载需要访问 GitHub 和 Google 模型下载服务。上游资源保持原样，保留 LICENCE.md（CC BY-NC-ND 4.0），来源与模型哈希详见 D 部分实施说明。下载脚本不会覆盖被手动修改的已有资源。

上述 CC BY-NC-ND 4.0 说明针对 SirAlexiner 手势项目，Google 模型和工具分别按其上游条款使用。下载失败时检查这两个来源的网络访问；不要手动换成同名但不同版本的模型，脚本和加载器会因 SHA256 不一致而拒绝使用。

以后需要修改默认微笑阈值，改 `smile_policy.py` 的 `DEFAULT_SMILE_THRESHOLD` 即可；当前为 **0.3**，独立笑脸与联合入口共用该值，重启程序生效。

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

## 笑脸检测

现有环境无需安装新依赖，使用已安装的 MediaPipe 0.10.35、NumPy 和 OpenCV，不需要 TensorFlow 或重新训练。单独调试可用各自入口；同时检测请使用下方联合入口，不要把两个独立程序同时连接同一个摄像头。

```powershell
# 下载官方预训练人脸模型并校验 SHA256
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
# 验证模型加载和无脸输入
.venv\Scripts\python.exe -m integration_tools.check_smile_model
# 启动摄像头笑脸检测，按 Q 或 Esc 退出
.venv\Scripts\python.exe -m emotion_interaction.smile_demo
```

模型为 Google 官方 `face_landmarker.task`，存放在 `emotion_interaction/models/face_landmarker.task`，来源为 [官方模型下载](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)，使用方式见 [官方说明](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python)。模型按原规则留在本地，队友运行下载脚本获取。

画面显示 SMILE、NO_SMILE、NO_FACE 或 MULTIPLE_FACES。smile_score 取左右嘴角微笑系数的较小值，是动作系数而非经过校准的“正在笑的概率”。默认阈值 0.3、连续确认 5 帧、冷却 5 秒，持续微笑只触发一次；释放 3 帧后才能重新确认。可以加 `--threshold 0.6` 调整阈值，需测试说话、张嘴、侧脸等误触发情况。无脸、多人、缺少系数、重复或过期帧不触发。

确认后本地生成原方案 `label=smile` 的六字段请求，默认 WAVE，添加 `--action NOD` 可改为点头。没有真实发送代码。加 `--mock-robot-state BUSY` 可观察模拟拒绝；模拟 ACCEPTED 仅代表接单，不是动作完成。终端打印 REQUEST_CREATED_LOCAL，日志存入 logs/smile-*.jsonl，不保存原始人脸图像。

更多配置和验证范围见 [笑脸检测说明](../docs/D_smile_detection.md)。

## 笑脸与手势一起检测

```powershell
.venv\Scripts\python.exe -m emotion_interaction.combined_demo
# 可选：命令行临时覆盖默认阈值
.venv\Scripts\python.exe -m emotion_interaction.combined_demo --smile-threshold 0.6
```

先关闭其他识别窗口，再启动联合入口。一个摄像头、一份原始帧，同时显示 Face 和 Hand 结果；两种检测共享 frame_id 和读取时间，独立连续确认与冷却，不要求“笑脸且做手势”才触发。默认笑脸阈值为 0.3，手势配置保持原值。

笑脸只创建原 label=smile 的本地请求；手势只生成本地候选，协议未扩展。没有真实机械臂控制，两个结果也不证明来自同一个人。窗口显示联合处理耗时；超过 500 ms 的旧帧不产生请求/候选。日志为 logs/combined-*.jsonl。

可用 --camera 1、--video "视频路径"、--mock-robot-state BUSY；按 Q 或 Esc 退出。原手势和笑脸独立入口仍保留。

联合窗口中绿色框标记人脸、青色框标记手部；两个框都只是定位结果，模型始终接收整张原始图像，绿框不会限制手势范围。Hand status 区分 NO_HAND（没检测到手）、MULTIPLE_HANDS（多手，抑制触发）、NON_TARGET_GESTURE（识别到非四类目标手势）、LOW_CONFIDENCE（分数不足）和 DETECTED（单帧有效，还需连续确认）。画面显示处理耗时和是否过期，日志保留 hand_boxes 方便对照。

## 接机械臂与答辩演示顺序

建议先分别演示稳定的手势和笑脸识别；第一次接真实机械臂时先接已有协议的笑脸请求，验证空闲接受、忙碌/夹物/故障/停止拒绝及过期/重复 ID。手势动作映射和协议由 B/C 确认后再接入，不能借用 smile 标签。最后再展示联合识别作为可选扩展，不要求两种识别同时出现才触发。

首次演示建议顺序：gesture_demo → 退出 → smile_demo → 退出 → 可选 combined_demo。若联合现场效果或处理速度不稳定，保留独立模式用于验收，不将离线通过当作真实机械臂安全验证。实际答辩要求最终以老师的验收口径为准。
