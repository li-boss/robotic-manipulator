"""单段语音的识别、动作分类和开发板授权执行流程。"""

from .actions import VoiceMode
from .text_classification.classifier import ActionPrediction
from .text_classification.commands import NO_COMMAND


def run_once(source, recognizer, client, log=print, classifier=None, voice=None, *, is_text=False) -> bool:
    """处理一段 WAV 或一条文字；最多分发一次动作。"""
    if classifier is None:
        raise ValueError("文本分类模型尚未接入")
    if not is_text and recognizer is None:
        raise ValueError("语音识别模型尚未接入")
    text = source if is_text else recognizer.transcribe(source)
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
    return (voice or VoiceMode(client, log)).handle(action, text)
