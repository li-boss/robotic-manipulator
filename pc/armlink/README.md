# 开发板通信与取帧

```text
armlink/
├── client.py       # HTTP 客户端
├── types.py        # 方块、手势和冻结帧数据
├── frames.py       # 共用取帧与插件加载
└── tests/          # 数据结构测试
```

`client.py` 封装开发板 HTTP 接口；`types.py` 定义方块、手势和冻结帧数据；`frames.py` 提供电脑摄像头、开发板摄像头和无画面来源，以及按 `模块:类名` 加载插件的工具。方块识别和表情手势包共用这里的接口。

测试放在 `tests/`。`BoardClient` 只依赖 Python 标准库；图像解码需要 NumPy 和 OpenCV，数据结构测试需要 NumPy。

```shell
python -m unittest discover -s pc/armlink/tests -p 'test_*.py'
```
