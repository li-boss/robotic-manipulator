"""语音文本到开发板动作标识的分类接口。"""

from dataclasses import dataclass
from importlib import import_module
from typing import Protocol


@dataclass(frozen=True)
class ActionPrediction:
    """action=None 表示拒绝执行；score 是可选的模型分数。"""

    action: str | None
    score: float | None = None


class ActionClassifier(Protocol):
    def predict(self, text: str) -> ActionPrediction:
        """对一次语音转写分类，无法确定时返回 action=None。"""
        ...


def load_classifier(spec: str) -> ActionClassifier:
    """从 module:Class 加载无参分类器；模型实现可使用自己的默认权重目录。"""
    module_name, separator, class_name = spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("分类器标识须为 module:Class")
    classifier = getattr(import_module(module_name), class_name)()
    if not callable(getattr(classifier, "predict", None)):
        raise TypeError("分类器须实现 predict(text) -> ActionPrediction")
    return classifier
