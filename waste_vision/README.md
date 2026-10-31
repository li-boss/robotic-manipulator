# 垃圾识别（A）

正式 YOLOv8n 由 `waste_vision.yolov8_backend` 在 Windows CPU 上运行。可选的 `waste_vision.top_face` 使用同帧原图、检测框和可配置 HSV 阈值计算彩色顶面四边形中心；当前为实验功能，须在服务启动时显式加 `--top-face`。旧框中心字段保持原义，顶面候选点在可选 `top_face` 字段。详见 [A 运行说明](../docs/a/运行与对接说明.md)和[顶面中心验证](../docs/a/顶面中心定位验证.md)。
