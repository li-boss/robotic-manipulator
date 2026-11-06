"""方块识别接口。方块识别同学：继承 BlockDetector，实现 detect()。"""
from abc import ABC, abstractmethod
from typing import List

from armlink import BlockDetection


class BlockDetector(ABC):
    """输入一张 BGR 图像（numpy uint8，H×W×3，开发板摄像头原图，当前 640×480），
    输出图中所有候选方块。不要缩放/裁剪后不还原坐标——返回的坐标必须是原图像素。

    运行器会取 score 最高的一个提交给开发板；没有方块就返回空列表。
    模型加载等耗时初始化放在 __init__ 里，detect() 会被反复调用。
    """

    @abstractmethod
    def detect(self, image) -> List[BlockDetection]:
        raise NotImplementedError
