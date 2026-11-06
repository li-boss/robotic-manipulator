"""表情/手势识别接口。表情手势同学：继承 GestureRecognizer，实现 recognize()。"""
from abc import ABC, abstractmethod
from typing import Optional

from armlink import GestureEvent


class GestureRecognizer(ABC):
    """输入一帧 BGR 图像（numpy uint8，来自电脑摄像头或开发板摄像头），
    识别到约定的表情/手势时返回 GestureEvent(label, confidence)，否则返回 None。

    运行器负责：取帧、连续多帧确认（防抖）、冷却时间、把 label 映射成跳舞动作并发给开发板。
    所以 recognize() 只管“这一帧是什么”，不用管机械臂。
    """

    @abstractmethod
    def recognize(self, image) -> Optional[GestureEvent]:
        raise NotImplementedError

    def close(self):
        """释放模型/资源（可选）。"""
