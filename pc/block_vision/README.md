# A 模块与新主线融合（2026-11-09）

本轮最新实机结果、真实叠加图、仅预览命令和组员对接状态见 [摄像头与接口验证记录](FIELD_VALIDATION_20261109.md)。该记录随本目录一并入库；`weights/` 下的原始图与全部现场证据按 `.gitignore` 只保留在本地。

## 当前状态

集成基线为远端 `main` 提交 `8322742`（2026-11-06）。新结构是 `board/` 负责相机、冻结帧、随臂定位、规划和控制，`pc/` 负责识别插件。相机安装方式据新版文档为**随机械臂移动**，不能使用固定桌面单应矩阵代替手眼定位。

原 A 工作区 `G:\robotic-manipulator` 保持原分支与服务；新版集成位于 `G:\robotic-manipulator-integration`，分支 `codex/a-block-vision-integration-20261109`。没有直接合并旧分支、恢复旧目录或修改 `board/` 的控制实现。D 的后续手势识别分支 `origin/codex/d-recognition-integration` 尚未并入此主线；本次仅复用其 URDF 固定 LF 的 `.gitattributes` 修复。

| 验证 | 本机实际结果 |
| --- | --- |
| 正式 YOLOv8n + 顶面算法迁移 | 23 张留存原图重新真实推理，23/23 帧的框、类别、置信度、框中心、完整 `top_face` 与旧输出逐项相同；共 25 个检测。迁移对照报告与重算脚本只保留在本地，不随仓库发布。 |
| 插件与新版运行器契约 | 输入 BGR 原图，输出 `BlockDetection` 顶面四角及中心；复核/低置信度不回退成框中心。电脑端 10 项测试通过，含本地 HTTP 模拟器的预测夹具→真实顶面模块→假规划/假舵机闭环。 |
| 主线整体回归 | 3 个网页脚本测试及 2 个 JS 语法检查通过。板端假设备测试运行 199 项，出现 5 处错误，均为本机 OpenCV 5.0.0 缺少 `cv2.calibrateHandEye`；不是实机运行记录。首次 CAD 哈希错误已通过固定 URDF 的 LF 检出解决。 |
| 真实 YOLO 在模拟器合成画面上 | 实际运行但没有候选点，没有提交模拟抓取。记录在 `weights/simulator_real_model_20261109_01.json`。不能把夹具测试写成真实模型模拟抓取成功。 |
| 定位精度 | 本次只证明迁移结果一致，不重新评价精度。旧 23 帧是开发资料，AI 四角不是人工确认真值。黄色前沿争议、相似侧面和遮挡仍有错误接受风险。 |
| 实机 | 未部署新板端代码，未调用运动或模式接口。需要 B 核对实际运行的是新版 `board/server.py`，以及标定、相机接入和当前设备配置。 |

## 插件和旧 API 的关系

`yolo_detector.YoloBlockDetector` 可被已有 `pc/run_block_grasp.py --detector block_vision.yolo_detector:YoloBlockDetector` 无参数加载。模型仅初始化一次并预热，模型与顶面调用在同一锁内串行。输入即调用者冻结帧解码后的原图，既不重新采帧，也不进行 JPEG 二次压缩；Ultralytics 的 `xyxy` 已在原图空间，不再还原一次。

- 默认 `imgsz=640`、YOLO `conf=0.25`、NMS IoU `0.5`、CPU。四类顺序固定为 `block_blue/green/red/yellow`。
- 顶面算法仍为 `top-face-contour-roi-v3`，HSV 文件与旧配置字节相同。只抽出 `locate_image()` 接收已解码的 BGR；分割、ROI、四边形检查及中心算法不变。
- `detect(image)` 只返回顶面状态为 `candidate` 且 YOLO 置信度≥0.5 的结果，保留旧 A 的低置信度保护。新版网页还会再按会话阈值过滤。YOLO 框与全部复核原因可从 `analyze(image)` 或 `last_result` 查看。
- 输出 `corners` 是沿轮廓排列的顶面四角，`center` 是对角线交点。没有普通框/默认角度回退。新版板端按顶面边处理方向，插件不输出关节角度、毫米坐标或高度。
- `score` 仅为 YOLO 检测置信度；四角可靠性和抓取成功均未由此证明。`label` 仅为颜色标签，不映射垃圾类别；诊断保留 `category:null,target_status:unmapped`。
- 新版 `BlockDetection` 不携带 `frame_id`，由现有运行器把同一次冻结的 `frame.frame_id` 与检测一起提交。插件不缓存或重放检测结果，也不把单帧序号当跨帧身份。
- 新插件是**显式选择的实验顶面检测器**。旧 A 服务的 `--top-face` 开关、8765 检测 API、mock 和 D 同帧原图接口仍留在原工作区；新主线的原始公共类型和运行器未修改。团队应先确认调用新版插件还是旧 A API，避免混用两套 `frame_id`。

选用插件不等于允许抓取。真实板端 `POST /api/v1/block/target` 会触发已授权会话中的运动，助手不得代替现场人员运行实机抓取主循环。

## 本机直接运行：先离线预览

在 PowerShell 执行，复用原 G 盘 Python 3.10.11 CPU 环境，无需重新安装：

```powershell
Set-Location 'G:\robotic-manipulator-integration'
$py = 'G:\robotic-manipulator\data\a_blocks\.venv-train\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$out = 'pc\block_vision\weights\preview_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
& $py pc/block_vision/preview.py --source 'G:\robotic-manipulator\data\a_blocks\camera_check_20261030_02\multiple_four\raw.jpg' --out $out
Invoke-Item (Join-Path $out 'overlay.png')
```

这个命令只读本地文件，输出同源原图、叠加图、逐目标复核原因、原图哈希、权重/配置摘要与插件实际输出，不连接板端。顶面处理耗时不含 JPEG 解码，`backend_ms` 包含 Ultralytics 预处理/推理/后处理，不可称纯模型耗时。`processing_ms` 为插件内一次分析耗时，不含取流、HTTP 或绘图。

权重已从原项目复制到本机 `pc/block_vision/weights/best.pt`，没有重新训练。SHA-256：`9a493d49070adae232cfc00f6b2e30965726f761b12367ede119d716c38d975a`。权重是本地训练产物，没有公开下载地址；换电脑时应由 A 提供并核验哈希。整个 `weights/` 已被新主线忽略，不上传模型、预览图片或缓存。也可用环境变量 `BLOCK_VISION_WEIGHTS` 指向原权重，`BLOCK_VISION_HSV_CONFIG` 指定配置副本。缺文件、类序错误或推理失败会明确报错，不下载其他权重或转用 mock。

依赖实测：Python 3.10.11、torch 2.7.1+cpu、torchvision 0.22.1+cpu、Ultralytics 8.4.183、NumPy 2.2.6、OpenCV 5.0.0.93。锁定文件为本目录 `requirements.txt`，本次未安装/升级依赖。GPU 与 Atlas 推理环境未验证。

## 测试与迁移重算（仅本地保留）

本目录只入库运行所需代码：`top_face.py`、`yolo_detector.py`、`preview.py`、`capture_preview.py`、`grasp_once.py`、`reach_help.py`、`vision_service.py`、HSV 配置与依赖清单。

测试与迁移验证材料（`pc/tests/test_block_*.py`、`validate_migration.py`、`check_simulator.py`、`migration_report_*.json`）按约定**不入库**，只保留在开发机上用于回归；其中的断言与结论仍适用于本目录代码，需要复核时由 A 在本机运行。

## 对接前还需确认

1. B 确认板端代码版本、手眼/内参、30 mm 积木和实际顶面高度处理、冻结图坐标及方向约定。新版文档说标定与人工抓放在用，并非本轮独立验证了当前设备的标定精度。
2. 当前已启动的旧纯摄像头服务只提供 `/status`、`/stream.mjpg`，不具备新版 `/api/v1/health`、冻结帧/模式协议；不能把插件运行器直接接到它。现场 USB 重新插接后已从 `/dev/video0` 取得真实 640×480 画面，并完成旧流到新版 A 的只读检测，见上方最新实测记录。由 B 核实并部署新版服务，本轮不替换正在运行的服务。
3. C/D 确认旧 A HTTP API 与新主线插件的过渡方案。D 后续手势分支没有自动合并、安装依赖或触发跳舞。规划服务默认找电脑 `.101:8095`，当前电脑是 `.102`；由 B 确认实际配置，不擅自改 IP 或防火墙。
4. 对不同随臂视角、完整/真正遮挡顶面、同色相邻、侧面及反光做新的人工四角复核。旧黄色争议点仍需确认；迁移一致不能证明四角正确，不应据本报告直接开启实机模式。
5. B 的手眼标定测试依赖当前 CPU 视觉环境中没有的 `cv2.calibrateHandEye`。正式联调前，B 应确认其板端 OpenCV 4.x 环境或单独标定测试环境；不要为了跑这 5 处测试直接替换正在使用的 CPU 视觉环境。未修改 B 源码，也未把错误标成测试通过。
