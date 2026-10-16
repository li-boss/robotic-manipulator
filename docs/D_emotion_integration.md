# D 手势识别与集成工具实施记录

## 当前范围

手势优先，笑脸后置。实现本地摄像头/视频识别、连续确认、防重复、冷却和离线替身测试。没有修改 contracts、A/B/C 实现、根依赖或真实硬件。原文档中的七接口仍是待四方评审的提案；本次只按其已有语义实现 D 侧可验证的子集。

## 模型和标签依据

- 上游：https://github.com/SirAlexiner/HandGestureRecognition
- 固定版本：f13db616a66f521882fac2d6c02322854ebb1343（原下载 ZIP 的版本注释）。
- 预训练模型：best_hand_gesture_classifier.pt，42,073,748 字节。
- SHA256：121ca234159eecb18c1b7c858285db8048d0157cc87bc3ddaedbda6d20d09298。
- 类别顺序：上游 images/confusion_matrix.png 的 24 类轴标签，结合 train.py 的 ImageFolder.classes 字母排序；没有采用 README 行顺序或 main.py 的四个备用标签。
- 项目内部映射：fist→fist、thumb_up→like、ok→ok、stop→palm。其他类别为非目标手势，不触发。
- 预处理：手部关键点→黑底白色骨架→200×200→灰度128×128→[-1,1]→分类分数0～1。使用现代 MediaPipe Tasks 定位手部；原 main.py 的旧 mp.solutions 接口在本机不可用。
- 官方手部定位模型：https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
- 定位模型 SHA256：fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1。
- 官方接口参考：https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python

上游许可为 CC BY-NC-ND 4.0，保留原文件和 LICENCE.md，未修改上游项目。当前权重与上游代码位于忽略的 models/ 目录，不随本项目提交。新增代码独立提供适配入口；不宣称模型是本人训练。

## 原互动接口保持原义

原方案 3.5：

| 字段 | 原约束 |
| --- | --- |
| interaction_id | 不可复用字符串；同 ID 重试不得重复动作 |
| label | smile |
| confidence | 0～1 |
| occurred_at / expires_at | Unix 毫秒，过期丢弃 |
| requested_action | WAVE 或 NOD |
| decision / reason | ACCEPTED 或 REJECTED，可附原因 |

gesture_policy 的 LOCAL_CONFIRMED 只是进程内候选，不是网络请求或业务事件。request_from_gesture 明确拒绝转换；不伪造 smile，不私自把 label 扩展为 fist/like/ok/palm。默认摄像头入口没有任何网络或机械臂发送代码。

mock 拒绝原因是测试用命名，除原方案明确给出的 ROBOT_BUSY 外，EXPIRED、ROBOT_HOLDING、ROBOT_FAULT 等须与 B/C 核对后才能作为正式枚举。机器人最后一次状态校验与真实动作互锁由 B 负责，D 的冷却不能代替互锁。

## 运行和环境

本机 Python 3.13.5 创建独立 .venv，include-system-site-packages=false，依赖按 emotion_interaction/requirements.txt 安装；没有全局安装或升级依赖。最初复用系统包时，MediaPipe 会导入系统 TensorFlow，导致启动缓慢；已改为独立环境，仅使用推理所需包，不安装 TensorFlow。OpenCV 只安装 opencv-contrib-python，避免多个发行包共享 cv2 目录。原仓库 README 的 Python 版本限制针对旧示例，当前新版适配器在本机 3.13 上已经完成无摄像头测试。

在仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m integration_tools.download_gesture_assets
.venv\Scripts\python.exe -m integration_tools.check_gesture_model
.venv\Scripts\python.exe -m integration_tools.contract_tests
.venv\Scripts\python.exe -m integration_tools.run_mock --scenario gesture
.venv\Scripts\python.exe -m emotion_interaction.gesture_demo
```

换机器时先创建 Python 环境、安装 emotion_interaction/requirements.txt，再下载资源。下载固定提交并校验两个权重哈希，不覆盖被手动修改的已有资源。所有启动命令使用 -m，避免工作目录导致的导入问题。

## 已验证与待验证

- 权重真实前向输出为 (1,24)。
- 原仓库 48 张骨架示例全部与标签表一致；报告 logs/gesture_model_check.json。
- 空白 BGR 图像通过完整手部定位入口，返回无手、不触发。
- 策略与协议、模拟依赖共 20 项测试通过：连续帧、释放/冷却、低分/未知、重复/乱序/过期帧、候选变更、原字段、阻止手势冒充笑脸、异常设备拒绝、重复 ID、事件断网/重启去重。
- 统一入口 13 个场景全部通过：gesture、normal、robot_busy、holding、fault、stopped、expired、duplicate、low_confidence、no_target、camera_down、expired_frame、net_down。
- 用生成的 3 帧空白 AVI 验证完整视频回放入口（--headless --max-frames 3），退出码 0，3 条日志全部为 no_hand、candidate=null；没有用此测试宣称真实摄像头效果。
- 隔离环境修复后再次验证：TensorFlow 不可见且未被导入，mediapipe 从项目 .venv 加载，pip check 无依赖冲突；20 项测试、48 张模型示例及本机非沙箱视频回放均通过。启动加入分阶段提示，Ctrl+C 退出不再输出长堆栈。
- 仓库示例可能来自训练数据，48/48 不能作为独立测试准确率；尚未使用老师数据集、现场人物/摄像头或真实机械臂验收。
- A/B/C 替身是方案语义子集，不是完整七接口测试；没有实现 B 分拣 action、状态/停止服务、B 持久 outbox 或 C 调度/看板。

## 下一步交接

1. 用户现场测试摄像头四手势、无手、其他手势、两手和不同背景，记录误触发与漏检。
2. A 确认图像实际载体和 captured_at 来源，D 再接入真实 frame_id、尺寸与 payload。
3. B/C 确认手势如何扩展原 label=smile 契约，以及手势与 WAVE/NOD 的映射；C 维护契约文件后 D 才实现真实发送适配器。
4. B 提供真实设备状态及交互请求入口，分别测空闲、忙碌、夹物、故障、停止、过期和重复 ID。
5. 继续扩充七接口契约样例与完整分拣场景；真实成功/失败与模拟结果分开记录。
