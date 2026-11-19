"""语音文本到命令类别的 TF-IDF + 逻辑回归分类器。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol

from .commands import NO_COMMAND, load_commands


DEFAULT_MODEL_PATH = Path(__file__).with_name("models") / "classifier.joblib"


@dataclass(frozen=True)
class ActionPrediction:
    """保留 NO_COMMAND 类别；action=None 表示分数不足或输入为空。"""

    action: str | None
    score: float | None = None


class ActionClassifier(Protocol):
    def predict(self, text: str) -> ActionPrediction:
        """对一次语音转写分类，无法确定时返回 action=None。"""
        ...


def build_pipeline():
    """构建可训练的字符特征与分类器；不做整句口令匹配。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(analyzer="char", ngram_range=(1, 3), sublinear_tf=True),
            ),
            ("classifier", LogisticRegression(C=30, max_iter=1000)),
        ]
    )


class IntentClassifier:
    """加载训练产物；保留无命令类别，低于验证阈值时拒绝分类。"""

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH):
        import joblib
        import sklearn

        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"分类模型不存在：{path}；先运行 text_classification.train"
            )
        saved = joblib.load(path)
        if not isinstance(saved, dict) or saved.get("format") != 1:
            raise ValueError("分类模型文件格式不受支持")
        if saved.get("sklearn_version") != sklearn.__version__:
            raise ValueError("分类模型与当前 scikit-learn 版本不一致，请重新训练")
        if saved.get("actions") != sorted(load_commands()):
            raise ValueError("分类模型的命令类别与 commands.json 不一致，请重新训练")
        threshold = saved.get("threshold")
        if type(threshold) is not float or not 0 <= threshold <= 1:
            raise ValueError("分类模型的拒绝阈值无效")
        pipeline = saved.get("pipeline")
        classes = getattr(pipeline, "classes_", ())
        if set(classes) != set(saved["actions"]):
            raise ValueError("分类模型的训练类别不完整")
        self.pipeline = pipeline
        self.threshold = threshold

    def predict(self, text: str) -> ActionPrediction:
        if not isinstance(text, str):
            raise TypeError("分类输入须为文字")
        if not text.strip():
            return ActionPrediction(None)
        probabilities = self.pipeline.predict_proba([text])[0]
        index = int(probabilities.argmax())
        action = self.pipeline.classes_[index]
        score = float(probabilities[index])
        if action == NO_COMMAND:
            return ActionPrediction(NO_COMMAND, score)
        if score < self.threshold:
            return ActionPrediction(None, score)
        return ActionPrediction(action, score)


def load_classifier(spec: str) -> ActionClassifier:
    """从 module:Class 加载无参分类器；模型实现可使用自己的默认权重目录。"""
    module_name, separator, class_name = spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("分类器标识须为 module:Class")
    classifier = getattr(import_module(module_name), class_name)()
    if not callable(getattr(classifier, "predict", None)):
        raise TypeError("分类器须实现 predict(text) -> ActionPrediction")
    return classifier
