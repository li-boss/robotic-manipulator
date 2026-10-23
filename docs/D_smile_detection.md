# D 笑脸检测接入说明

## 实现范围和环境

在已有 feature/d-gesture 分支新增独立笑脸入口，不改手势行为、不改 contracts。使用当前独立 .venv 的 Python 3.13.5、MediaPipe 0.10.35、NumPy 2.4.3、opencv-contrib-python 5.0.0.93；没有新增依赖，没有继承系统 TensorFlow，也没有训练模型。

Face Landmarker 输出人脸关键点和面部动作系数，本功能只判断是否满足微笑规则，不推断人的真实情绪。num_faces=2 用于发现多人并抑制请求；IMAGE 模式每帧同步运行，连续确认由本地策略完成。

## 模型来源和下载

- 官方项目：https://github.com/google-ai-edge/mediapipe
- 官方 Python 示例：https://github.com/google-ai-edge/mediapipe-samples/tree/main/examples/face_landmarker/python
- 官方 API：https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python
- 固定模型版本：https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
- 本地位置：emotion_interaction/models/face_landmarker.task。
- SHA256：64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff。

模型不提交 Git，下载脚本固定版本并校验哈希，发现已有文件不一致时拒绝覆盖。新机器先按 emotion_interaction/README.md 创建环境、安装依赖，再执行：

```powershell
.venv\Scripts\python.exe -m integration_tools.download_smile_assets
.venv\Scripts\python.exe -m integration_tools.check_smile_model
```

下载需要访问 Google 官方模型服务。下载的预训练权重来源为 Google，本项目不宣称自行训练或拥有上游模型；代码与权重的许可应分别遵循上游提供的条款。

## 摄像头与视频运行

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo
# 指定摄像头、阈值和请求动作
.venv\Scripts\python.exe -m emotion_interaction.smile_demo --camera 1 --threshold 0.6 --action NOD
# 原协议模拟忙碌拒绝
.venv\Scripts\python.exe -m emotion_interaction.smile_demo --mock-robot-state BUSY
# 本地视频回放
.venv\Scripts\python.exe -m emotion_interaction.smile_demo --video "视频路径"
```

按 Q 或 Esc 退出，Ctrl+C 也可停止。不要把手势和笑脸两个独立入口同时连接同一摄像头；需要同时检测时运行 `python -m emotion_interaction.combined_demo`，由一个入口共享视频源。日志只存帧 ID、读取时间、微笑系数、脸框和请求/模拟结果，不保存原始人脸图片；logs/ 被 Git 忽略。当前 captured_at 是本地读取时间，回放不能据此计算原始采集端到端时延；后续接 A 时使用 A 的真实 frame_id 和采集时间。

联合入口两种检测读取同一份未画标记的原始图像，共用 frame_id/captured_at，完成后分别做策略确认。不是必须同时笑和举手才确认，不合并两种动作请求。该入口不做人脸与手势的身份关联。新增同帧、独立触发、保持不重复及联合超时测试，全部自动测试共 37 项通过。

## 策略与原接口

smile_score = min(mouthSmileLeft, mouthSmileRight)，默认两侧均达到 0.3 才计为候选。该系数是模型的面部动作强度输出，不是校准过的分类概率。原接口 confidence 填入本次连续确认窗口的最低 smile_score，仍为 0～1；该测量口径已明确记录，接 B/C 时需要共同确认阈值，不能把它解释成概率准确率。

默认连续 5 个有效帧确认、冷却 5000 ms、释放 3 帧、帧有效期 500 ms、请求有效期 1000 ms。无脸、多人、无效系数或低于阈值打断候选；重复/过期/未来/乱序帧、时钟回退也不触发。脸框大幅跳变会重启候选计数，但不具备身份识别能力，不宣称保证相邻帧是同一个人。

确认后调用已有 make_smile_request，字段严格保持为：

```json
{
  "interaction_id": "唯一ID",
  "label": "smile",
  "confidence": 0.8,
  "occurred_at": 1792100000500,
  "expires_at": 1792100001500,
  "requested_action": "WAVE"
}
```

时间均为 Unix 毫秒，动作仅 WAVE/NOD。当前只本地创建请求，可选调用现有 FakeRobot，没有连接真实设备。B/C 确认传输方式后才接 ROS2 service/action；真实设备状态和执行前互锁由 B 实现。原契约不增加 face_count、smile_score 或脸框等字段，这些仅保存在本地诊断记录中。拒绝和过期不排队，超时不能生成新 ID 盲重发。

## 验证和后续

新增 13 项笑脸证据/策略测试，加上原 20 项共 33 项自动测试通过：左右系数、缺少/非法分数、无脸/多人、连续确认、持续保持不重发、释放/冷却、新 ID、过期/重复/乱序帧、脸框变化、原六字段请求及各设备状态模拟拒绝。

真实模型已完成加载和空白输入测试，完整视频入口用生成的 3 帧空白 AVI 跑通，无人脸、无请求。官方 Python 示例使用的 business-person.png 也完成真实推理：检测到 1 张脸，左右微笑系数约 0.649/0.549。

官方测试图片来源：https://storage.googleapis.com/mediapipe-assets/business-person.png 。仅下载到被忽略的 tmp/，不随代码提交；来源依据为官方示例 notebook 的 Download test image 单元。

用该示例重复生成的 10 帧 AVI 完成程序链路测试，日志只有 1 次 REQUEST_CREATED_LOCAL，原六字段请求通过 FakeRobot 接单验证。重复静态图片仅用于软件接线测试，不能作为真实持续微笑、防抖或独立准确率的证据。尚未使用老师数据集或现场人物进行准确率评估，也未做真实机械臂联调。

用户下一步现场检查：不笑、微笑、大笑、说话、打哈欠、侧脸、低光、多人；确认笑脸才创建本地请求，持续保持不重复。阈值 0.3 是当前默认值，不是验收指标。老师数据集可用来评估规则，需要调整阈值时划分验证与独立测试样本；先不额外训练。

联合窗口补充了青色手部框、绿色人脸框、手势检测状态与处理耗时。两个模型接收同一整帧，框仅用于推理后的显示，不是搜索区域。新增测试验证画面侧边的手仍使用完整帧定位、多手保留定位框但不触发、无手/低分/非目标等状态分开，当前自动测试共 40 项通过。

首次接机械臂与答辩建议采用分开演示的稳定入口；联合模式作为可选展示，现场验证后再使用。既不要求必须联合，也不擅自定义“同时微笑并举手”才触发的条件；最终课程要求由老师确认。
