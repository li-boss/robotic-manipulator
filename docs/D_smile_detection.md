# 笑脸检测实现说明

## 检测规则

笑脸使用 MediaPipe Face Landmarker。`smile_score` 取 `mouthSmileLeft` 与 `mouthSmileRight` 的较小值；它是面部动作系数，不是经过校准的概率。

默认规则：

- 阈值 0.3；
- 连续确认 5 帧；
- 释放 3 帧；
- 冷却 5000 ms；
- 最大帧年龄 500 ms；
- 无脸、多人、无效系数、重复帧和乱序帧不触发。

确认后生成 D 侧触发记录：

```json
{
  "request_id": "32位十六进制编号",
  "action": "WAVE",
  "confidence": 0.72,
  "detected_at": 1792100000000
}
```

真正发送给 JJT 的请求严格使用 JJT `/control/states/execute` 字段。

## 模型

- 人脸模型：Google MediaPipe `face_landmarker.task`；
- 手部定位：Google MediaPipe `hand_landmarker.task`；
- 手势分类：SirAlexiner/HandGestureRecognition 固定版本。

模型不提交 Git，通过 `integration_tools/download_*_assets.py` 下载并校验哈希。

## 限制

- 不进行身份识别；
- 笑脸和手势框不表示二者属于同一个人；
- WAVE/NOD 的关节参数必须来自 JJT 已保存并由操作者核对的姿态；
- 软件测试通过不代表实机动作或路径已经验收。
