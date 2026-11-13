# A 摄像头与组员接口验证（2026-11-09）

先打开本次新实拍的 [四块同框叠加图](weights/field_20261109_214803/multiple_blocks/overlay.png)。全部证据位于本目录已忽略的 `weights/field_20261109_214803/`，含原图、原图 SHA-256、PC 接收时间、真实模型输出、四角/中心及复核原因。

## 当前结果

| 链路 | 实际状态 | 本轮证据 | 剩余阻塞 |
| --- | --- | --- | --- |
| 现场相机出图 | 已取得真实 640×480 JPEG。USB 重新插接后识别 Microdia/Sonix Camera，一台相机有 video0/video1，服务实际采集 video0。 | `board_after_usb_insert.txt`、各场景 `capture.json/raw.jpg` | 初次旧流中断，进程退出原因未确定；由用户重新启动已有纯相机服务后恢复，未擅自重启。 |
| 旧流实拍 → 新版 A | 已完成 7 个单次场景和 15 帧连续检测，真实 CPU YOLO、相同权重/HSV/参数。 | `scene_results.json`、`multiple_blocks/overlay.png`、`continuous_current_scene/summary.json` | 检测漏检/背景误检、低置信度和完整蓝面过度复核仍存在。没有人工四角真值，不评价精度达标。 |
| 新版冻结帧 → A | 独立仅预览入口及单元测试完成；现场未完成。 | `frozen_protocol_check/capture.json`、`test_results.json` | 现场 `/api/v1/health` 返回 404。已有新版代码文件，但运行的仍是 camera_preview。新版冻结接口还要求已经开启 block_grasp 会话，本轮不为取图开启运动模式。 |
| 其他模块对接 | 已检查最新远端分支，在独立工作区试融合 A+D，25 项 PC 测试通过（跳过 1 项 D 实模加载测试）。 | `remote_branch_inventory.json`、`trial_a_file_identity.json`、`test_results.json` | D 真实模型资产/独立运行环境及 B 新服务未现场验证；C 的实际维护边界需本人确认。未进行成员实机联调。 |

电脑网卡为 ASIX“以太网 3”，`192.168.137.102/24`，板端使用既有配置 `192.168.137.100:8080`。旧 A 的 `127.0.0.1:8765` 本轮检查时未运行；新版插件直接接收图像，不依赖它。

## 实际场景观察

以下逐图原图和结果均已保存。除场景摆放由用户回复就绪外，对象可见性和问题分类为 AI 图像观察，`human_confirmed=false`，没有冒充人工四角审核。

| 记录目录 | 图像观察 | YOLO 框 / A 候选 | 具体问题 |
| --- | --- | --- | --- |
| `first_fresh_analysis` | 水壶、绿色瓶子背景，未见积木 | 2 / 0 | 绿色瓶子被误报 green=0.926，顶面复核；壶附近 blue=0.279，同时低置信度和无颜色区域。不是干净工作区空场景。 |
| `single_block` | 红块底部越出画面 | 0 / 0 | YOLO 未出框，检测阶段漏检；没有运行该目标的顶面定位。 |
| `single_red_center` | 完整红块 | 1 / 0 | red=0.300；内部顶面四角/中心通过，但低于既有 0.5 候选门槛，不能称为几何检查失败。 |
| `multiple_blocks` | 蓝绿红黄四块完整入镜 | 4 / 3 | green=0.884、yellow=0.841、red=0.834 输出顶面候选。blue=0.831 因分割非凸复核；原图顶面看起来完整，不能把 reason 文案当作已证实遮挡。 |
| `empty_workspace` | 用户确认移走积木，AI 看原图无积木 | 0 / 0 | 正常无目标，没有推理异常。 |
| `partial_top_occlusion` | 卡片贴近蓝块上缘，遮挡程度不够明确 | 2 / 1 | 黄候选、蓝复核；不计作可靠的完整顶面遮挡案例。 |
| `yellow_half_occluded` | 卡片明显覆盖黄色顶面；蓝块完整 | 2 / 0 | yellow=0.299，低置信度且 quad_unreliable；blue=0.848，small_color_region。未把遮挡残片作为候选，但完整蓝面仍被复核。 |

四块同框中的顶面中心（原图像素）：绿 `(178.543,207.343)`、黄 `(351.082,83.604)`、红 `(350.345,215.558)`。四角在 `scene_results.json` 和各自 `result.json` 中。这只是算法输出，尚无对应人工参考角点或真实标定精度证据。未发现可直接确认“候选四角选到侧面”的案例，不等于排除了这种风险，黄色前沿等仍需人工检查。

## 连续取流的统计口径

预热 2621.597 ms 后，连续处理 15 张新 JPEG，无取流异常，耗时 1.822 s、约 8.23 处理帧/秒。循环中位数 124.46 ms、P95 141.14 ms，包括取流等待、JPEG 解码、后端、顶面、绘图与保存。后端中位数 38.67 ms、P95 46.43 ms，包含模型预处理和后处理，**不是纯神经网络时间**；顶面中位数 0.959 ms、P95 1.219 ms。

连续段原图显示卡片已移开，因此独立记为完整蓝黄双块场景，不并入黄色遮挡记录。黄色 15 帧均输出候选；蓝色 14 帧候选、1 帧复核。保存全部原始点和图像，未平滑、未跟踪，也未把局部目标序号作为跨帧身份。只有短段观察，不代表长时间运行已验收。PC 接收时间不等于板端曝光时间，JPEG 哈希不同也不能证明没有上游积压。

## 新版仅预览入口

新增 `capture_preview.py`，复用已有 `BoardClient`、`YoloBlockDetector.analyze()` 和预览绘图。默认先确认新版健康协议，再读取 `/api/v1/frame.jpg`；仅当新版健康接口明确 404 时回退旧 `/status` 和 `/stream.mjpg`。完整 JPEG 取不到会报错，不发布旧图。模型和相机错误不伪装成无目标。

在 PowerShell 输入：

```powershell
Set-Location 'G:\robotic-manipulator-integration'
$py = 'G:\robotic-manipulator\data\a_blocks\.venv-train\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$out = 'pc\block_vision\weights\capture_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
& $py pc/block_vision/capture_preview.py --out $out --scene current
Invoke-Item (Join-Path $out 'overlay.png')
```

程序保存一次后退出，不提交抓取目标，不切换模式，不发送舵机指令。原始 JPEG 和模型分析使用同一次图像；旧流及新快照没有冻结编号时，`frame_id=null`，不制造新版编号。

可选 `--freeze` 只在新版服务已存在空闲 block_grasp 会话时调用现有 `client.freeze_frame()`，原样保留该响应的 `frame_id/jpeg/image_size/joints/captured_at`。会话未开则明确拒绝，不自动开启。本轮实机仅检测到健康接口 404，所以没有发送冻结 POST，更没有发送 `/api/v1/block/target`。若要求严格不启用运动会话，B 需提供独立只读冻结入口或导出冻结证据后才能完成此项。

## 组员接口核查与试融合

最新 `main=8322742`；`jjt=192deae` 和 `feature/d-smile=4bf5f56` 已是 main 的祖先。`feature/d-new=6665cbc` 为旧结构独立改动，未并入 main，不将它直接混入新结构。最新 D 模型集成 `codex/d-recognition-integration=14bb2ad` 基于最新 main，尚未合入 main。

| 实际代码 | 输入与输出、调用关系 | 主线情况 |
| --- | --- | --- |
| `board/server.py` / `board/modes.py` / `pc/armlink`（机械臂控制接口） | 健康/模式/任务为 GET；`block/frame` 冻结图+真实读数；A 输出四角后，原抓取运行器会提交 `block/target` 并自动运动，所以本轮不运行它。 | 在 main。现场尚未运行该新服务。B 需确认部署与只读冻结方案。 |
| 任务与事件代码 | 模式/任务由 `board/modes.py`、`GET /api/v1/mode`、`GET /api/v1/job` 提供；本地事件由 `board/events.py` 保存。新主线没有旧独立 task_service/event_service/contracts。 | 代码在 main，但不能仅凭目录推定 C 已完成对接。请 C 确认这些是否作为其实际消费入口，以及维护人。 |
| D 的 `pc/gesture/recognizer.py` / `pc/run_gesture_dance.py` | `BoardSource` 调用 `BoardClient.snapshot()` 获得最新完整 BGR 原图；识别器返回 `GestureEvent`，运行器经模式授权调用 gesture 端点。未消费旧 8765 检测 API。 | 新模型在 14bb2ad 独立分支；main 仅有键盘示例与运行器。 |
| 新 A 插件 | 抓取运行器把一次冻结响应解码成 BGR，传给 A，结果与原 `frame_id` 一起发送。D 独立读取快照，当前并不与 A 共享冻结编号。 | A 保持未提交；若以后要求 A/D 同帧，应由 C/B 明确帧契约，不能套用旧 API 编号。 |

独立试融合位于 `G:\robotic-manipulator-ad-check`，分支 `codex/a-d-preview-20261109`。先应用原暂存快照，再复制本轮仅预览入口/测试，随后试应用 D 增量；6 个关键 A 文件逐一验证字节一致。仅 `.gitattributes` 出现 add/add，保留 D 的模型 LF 规则并补 A 的 HSV LF 规则，其余 D 模块保持推送版本。25 项 PC 测试通过，跳过 1 项因 D 实际权重未安装的实模测试；不能写成 D 实模已验证或组员现场联调完成。该试验没有回灌原暂存区，没有提交或推送。

D 依赖文件要求 Python 3.13.5、torch 2.13.0、NumPy 2.4.3 等；A 可用环境为 Python 3.10.11、torch 2.7.1+cpu、NumPy 2.2.6。以 D 推送的模型/标签 manifest 和依赖版本为其模块基准，不拿 A 的版本覆盖；两套模型应使用各自环境、独立进程，不能把冲突的精确依赖强装进当前 A 环境。本轮未安装或升级依赖。

## 测试与保留事项

主 A 工作区当前 PC 测试 15 项通过；新增针对仅预览的 5 项检查覆盖快照不冻结、不改模式；同一次冻结 ID/图像/尺寸一致；未开启会话拒绝；旧流不制造 ID；JPEG 不完整不变成空场景。原板端假设备测试的 5 处 `calibrateHandEye` 错误保持单列：已知失败发生在电脑 A 的 OpenCV 5 环境，不能据此断言板端实际 OpenCV 也缺少该功能；板端标定环境需 B 核实。

原 11 个暂存文件、HEAD 和分支保留，`workspace_preservation.json` 对 index 与 staged patch 的 SHA-256 验证均相同。本轮新增代码/文档保持未暂存；没有 commit、push、pull、切换原分支或覆盖其他组员实现。

B 后续需确认：让已部署的新服务接管 8080（当前被旧纯相机服务占用，不能另开进程抢摄像头）；无运动模式下的冻结取图/导出方式；板端与电脑时钟约 8 小时偏差的来源（本轮只记录两端原时间，不修改）；实际内参/手眼与标定运行环境。C 只需明确消费接口及是否要求 A/D 同帧。D 只需确认其权重 manifest、独立环境及试融合变更，现有模型版本优先于本地假设。
