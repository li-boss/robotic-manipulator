# D：手势、微笑识别接入

本目录以 GitHub main `8322742` 为基础，迁入 `feature/d-smile` 的检测逻辑。
模型在 Windows 电脑 CPU 上运行，开发板负责动作；不需要把 torch、mediapipe 安装到开发板。
识别器不自行发 HTTP 请求，不提交关节角度；复用 `pc/run_gesture_dance.py` 和 `BoardClient`。

## 1. 环境与权重

已沿用的环境：Windows、Python **3.13.5**、CPU 推理。请伙伴优先用相同 Python 版本。
在仓库根目录执行；已有可用 `.venv` 时跳过创建，不需要激活环境：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r pc\gesture\requirements.txt
.venv\Scripts\python.exe pc\gesture\download_assets.py
```

`requirements.txt` 固定原来实际使用的 torch、mediapipe、numpy、opencv-contrib-python 版本。
该依赖文件已经覆盖电脑端 numpy/OpenCV 需求。不要再同时安装 `pc/requirements.txt` 中的
`opencv-python`，两个 OpenCV 包共用 `cv2`，混装可能冲突。已有环境可先用
`.venv\Scripts\python.exe -m pip list` 检查；不要删除或重建目前可用的环境。

模型不提交 Git，也不提交 `.venv`。下载脚本按下面固定地址下载并验证 SHA-256；
已有文件散列不匹配就报错，不覆盖。复制本机旧权重也会做同样校验：

```powershell
.venv\Scripts\python.exe pc\gesture\download_assets.py --from-local emotion_interaction\models
```

所有权重统一放进 `pc/gesture/weights/`（main 已忽略此目录）：

| 文件 | 来源与用途 |
|---|---|
| `best_hand_gesture_classifier.pt` | [SirAlexiner/HandGestureRecognition 固定提交](https://github.com/SirAlexiner/HandGestureRecognition/tree/f13db616a66f521882fac2d6c02322854ebb1343)，TorchScript 手势分类器 |
| `hand_landmarker.task` | [Google MediaPipe Hand Landmarker float16/1](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task)，手部关键点 |
| `face_landmarker.task` | [Google MediaPipe Face Landmarker float16/1](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)，面部关键点及表情系数 |

对应 SHA-256：

```text
best_hand_gesture_classifier.pt 121ca234159eecb18c1b7c858285db8048d0157cc87bc3ddaedbda6d20d09298
hand_landmarker.task fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1
face_landmarker.task 64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff
```

分类器直接下载地址由 `assets.py` 的固定 GitHub 提交构造；同文件保存全部 URL 和散列。
上游分类器许可证在其仓库中注明为 CC BY-NC-ND 4.0；使用及分发时保留来源并核对上游许可。

## 2. 识别结果与动作

| 标签 | 含义 | 本次推荐映射 |
|---|---|---|
| `smile` | 双侧嘴角微笑系数最小值 ≥0.2（跳舞预设） | `course` 课程舞蹈 |
| `palm` | 张掌，分类器原标签 `stop`，置信度 ≥0.8 | `music` 音乐舞蹈 |
| `fist` | 握拳，置信度 ≥0.8 | 仅识别 |
| `like` | 点赞，原标签 `thumb_up`，置信度 ≥0.8 | 仅识别 |
| `ok` | OK，置信度 ≥0.8 | 仅识别 |

微笑分数是 `min(mouthSmileLeft, mouthSmileRight)`，不是经过校准的概率。
手部模型先提取关键点，再画黑底白色手骨架，按原模型预处理送入分类器；不是直接分类原图。
两个检测器都看同一整帧，不用脸框裁剪手部区域。
一个有效手势优先于微笑，两个分数不互相比较；多手不产生手势事件，多脸不产生微笑事件。
接口每帧只返回一个事件，原始分数不改写。没有目标或输入为空返回 `None`。
`wave` 不是现有分类器的目标标签；旧文档的 `snake` 已被 main 替换为 `course`。

## 3. 先看识别（不连接机械臂）

```powershell
.venv\Scripts\python.exe pc\gesture\preview.py
.venv\Scripts\python.exe pc\gesture\preview.py --mode gesture
.venv\Scripts\python.exe pc\gesture\preview.py --mode smile
```

默认电脑摄像头 0；换第二个摄像头加 `--camera 1`。画面与终端显示识别结果，Q 退出。
要检查机械臂上的开发板摄像头，运行：

```powershell
.venv\Scripts\python.exe pc\gesture\preview.py --source board
```

它只读取开发板最新画面并在电脑上识别，不开启模式、不发送机械臂动作。画面与终端显示识别结果，Q 退出。

## 4. 模拟联调

终端一：

```powershell
.venv\Scripts\python.exe board\simulate.py
```

浏览器打开 `http://127.0.0.1:8092/modes`，选择表情手势跳舞、模拟竖直起点，
允许 course 和 music，预览并开启模拟模式。终端二：

```powershell
.venv\Scripts\python.exe pc\run_gesture_dance.py --board http://127.0.0.1:8092 --recognizer gesture.recognizer:CombinedRecognizer --source webcam --map smile=course,palm=music --stable 5 --threshold 0.3
```

上面的模拟命令显式使用微笑阈值0.3和 `--stable 5`。实机默认使用 `DanceRecognizer`：
手势阈值0.8、微笑阈值0.2，运行器阈值也默认为0.2；若显式把运行器阈值设得更高，低分微笑会被再次过滤。
实机 `--stable 3` 表示累计3次有效识别确认，期间容忍单帧漏检；连续两帧未识别会重置计数。
触发后保持同一标签不会反复触发，需先撤掉手势/停止微笑。
三个未映射手势会被运行器记录为“没有映射到动作，忽略”；联合检测中仍占手势优先级。
单独接微笑可用 `gesture.recognizer:SmileRecognizer`，单独手势可用 `gesture.recognizer:HandRecognizer`。

## 5. 现场操作（由在场的人执行）

先请 B 确认 `/root/jjt` 的服务版本、动作模板、起始姿态和现场空间条件。
电脑模型仍用上述 Windows 环境；截图中的开发板 Python 3.9.2 不必与电脑一致，也不用给它装识别模型。
浏览器能打开 `http://192.168.137.100:8080/api/v1/health` 后，
在 `/modes` 页面预览动作的全部参数，开启有限次数的表情手势跳舞会话。
课程舞蹈需要六关节90°的已保存起点，以页面检查结果为准，不照抄旧文档的蛇形起点。

由在场的人在电脑终端执行：

```powershell
.venv\Scripts\python.exe pc\run_gesture_dance.py --board http://192.168.137.100:8080 --source board
```

实机运行默认已配置为电脑端模型 + 开发板摄像头、`smile=course,palm=music`、累计3次有效识别、微笑阈值0.2。
如需改用电脑摄像头，显式传入 `--source webcam`。网页会话仍须由现场人员预览并开启；
不开启时电脑端运行器会等待，不提交任何动作。
跳舞期间 B 的运行器等待任务结束，不继续识别；完成后才恢复。
终端显示触发标签、分数、动作名称及最终任务状态；模型输出本身不代表实机动作成功。
网页关闭模式或 Ctrl+C 请求停止后续动作，已经下发的动作无法撤回，不是硬件急停。
请求结果未知或超时时检查页面与现场，不要重复提交。

## 6. 离线验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s pc\tests -p "test_gesture_*.py" -v
```

包括标签/阈值/空帧/优先级/资源释放、真实模型空背景检查，以及本机127.0.0.1假机械臂的
“微笑→course、张掌→music”HTTP联调。缺少权重时真实模型检查跳过，其余逻辑测试仍执行。
模拟任务 completed 只证明软件链路，不证明真实机械臂到位。实机动作仍待现场验收。

Windows 检出时根目录 `.gitattributes` 固定 URDF 为 LF，避免 Git 自动转 CRLF 造成
“CAD参数与课程模型版本不一致”。该规则不修改模型内容或模型散列，不跳过版本校验。

本次本机验证结果：7项适配逻辑测试、1项权重保护测试、1项真实模型加载与空背景测试、
1项本地模拟HTTP测试（含两套动作）、main已有4项电脑端测试均通过，共14项。
模型权重均通过上列散列校验。尚未由助手打开真实摄像头验收识别效果，也未连接或驱动真实机械臂。
