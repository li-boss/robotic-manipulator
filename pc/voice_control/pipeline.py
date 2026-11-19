"""单段语音的识别、动作分类和开发板授权执行流程。"""

from .actions import execute
from .text_classification.classifier import ActionPrediction
from .text_classification.commands import NO_COMMAND


def run_once(wav_file, recognizer, client, log=print, classifier=None) -> bool:
    """每段音频最多提交一次动作；分类器拒绝时不访问开发板。"""
    if classifier is None:
        raise ValueError("文本分类模型尚未接入")
    text = recognizer.transcribe(wav_file)
    log(f"识别文字：{text or '（空）'}")
    if not text or not text.strip():
        log("未识别到指令")
        return False
    prediction = classifier.predict(text)
    if not isinstance(prediction, ActionPrediction):
        raise TypeError("分类器须返回 ActionPrediction")
    action = None if prediction.action == NO_COMMAND else prediction.action
    if action is None:
        log("未识别到指令")
        return False
    if not isinstance(action, str) or not action:
        raise ValueError("分类器返回的动作标识无效")
    log(f"匹配动作：{action}" + (f"（分数 {prediction.score:.3f}）" if prediction.score is not None else ""))
    return execute(action, client, text, log)
