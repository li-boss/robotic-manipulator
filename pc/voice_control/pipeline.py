"""单段语音的识别、动作分类和开发板授权执行流程。"""

from pc.armlink import BoardError
from .text_classification.classifier import ActionPrediction
from .text_classification.rules import RuleClassifier


def run_once(wav_file, recognizer, client, log=print, classifier=None) -> bool:
    """每段音频最多提交一次动作；分类器拒绝时不访问开发板。"""
    text = recognizer.transcribe(wav_file)
    log(f"识别文字：{text or '（空）'}")
    if not text or not text.strip():
        log("未识别到指令")
        return False
    prediction = (classifier or RuleClassifier()).predict(text)
    if not isinstance(prediction, ActionPrediction):
        raise TypeError("分类器须返回 ActionPrediction")
    routine = prediction.action
    if routine is None:
        log("未识别到指令")
        return False
    if not isinstance(routine, str) or not routine:
        raise ValueError("分类器返回的动作标识无效")
    log(f"匹配动作：{routine}" + (f"（分数 {prediction.score:.3f}）" if prediction.score is not None else ""))

    mode = client.mode()
    if not mode.get("armed") or mode.get("kind") != "gesture_dance":
        raise BoardError("请先在开发板 /modes 页面预览并开启表情手势跳舞模式")
    if routine not in mode.get("routines", ()):
        raise BoardError(f"本次会话未授权动作：{routine}")
    if mode.get("busy"):
        raise BoardError("开发板正在执行其他任务，本次语音不提交动作")
    session_id = mode.get("session_id")
    if not session_id:
        raise BoardError("当前模式缺少会话编号，本次语音不提交动作")

    result = client.dance(session_id, routine, label=text)
    job = client.wait_job(result["job"]["id"])
    status = job.get("status")
    log(f"动作结果：{status} {job.get('message') or job.get('error') or ''}".rstrip())
    if status != "completed":
        raise BoardError(f"动作未完成：{status}；不会自动重试")
    return True
