"""语音命令的执行入口；新增动作须在对应函数中检查参数与会话授权。"""

import math
import os
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pc.armlink import BoardError
from pc.armlink.types import BlockDetection

from .text_classification.commands import NO_COMMAND, load_commands


def _dance(client, text, log, routine):
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


def music_dance(client, text, log):
    return _dance(client, text, log, "MUSIC_DANCE_ROUTINE")


def course_dance(client, text, log):
    return _dance(client, text, log, "COURSE_DANCE_ROUTINE")


def _not_implemented(action, log):
    log(f"该指令尚未接入执行流程：{action}")
    return False


def pose_vertical(client, text, log):
    return _not_implemented("POSE_VERTICAL", log)


def pose_observe(client, text, log):
    return _not_implemented("POSE_OBSERVE", log)


def set_servo_abs_angle(client, text, log):
    return _not_implemented("SET_SERVO_ABS_ANGLE", log)


_COLORS = {"红": "red", "黄": "yellow", "蓝": "blue", "绿": "green"}


def _requested_color(text):
    colors = {name for word, name in _COLORS.items() if f"{word}色" in text or f"{word}方块" in text or f"{word}积木" in text}
    if len(colors) > 1:
        raise ValueError("语音中出现多种颜色，无法确定要抓的方块")
    return next(iter(colors), None)


def _detect_blocks(frame):
    """把同一张冻结帧交给独立 YOLO 环境；校验返回结果绑定的图像。"""
    endpoint = os.environ.get("VOICE_CONTROL_VISION_URL", "http://127.0.0.1:8096").rstrip('/') + '/infer'
    request = Request(endpoint, data=frame.jpeg, method='POST',
                      headers={'Content-Type': 'image/jpeg'})
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get('error', str(exc))
        except (ValueError, AttributeError):
            message = str(exc)
        raise BoardError(f'视觉服务拒绝推理：{message}') from None
    except URLError as exc:
        raise BoardError(f'连接视觉服务失败：{exc.reason}') from None
    if result.get('image_sha256') != hashlib.sha256(frame.jpeg).hexdigest():
        raise BoardError('视觉服务返回结果与本次冻结画面不匹配，未提交抓取')
    rows = result.get('detections')
    if not isinstance(rows, list):
        raise ValueError('视觉服务未返回检测结果列表')
    detections = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('视觉服务返回的检测结果无效')
        detection = BlockDetection(**row)
        detection.to_dict()
        detections.append(detection)
    return detections


def start_block_grasp(client, text, log):
    """一次语音最多提交一个方块；目的地由网页已授权的会话决定。"""
    if any(phrase in text for phrase in ("放到", "放进", "放入", "投放到")):
        log("语音指定放置区域尚未支持；请在网页抓取会话中预选固定放置点")
        return False
    color = _requested_color(text)
    mode = client.mode()
    if not mode.get("armed") or mode.get("kind") != "block_grasp":
        raise BoardError("请先在开发板 /modes 页面预览并开启方块识别自动抓取模式")
    if mode.get("busy"):
        raise BoardError("开发板正在执行其他任务，本次语音不提交抓取")
    session_id = mode.get("session_id")
    if not session_id:
        raise BoardError("当前抓取模式缺少会话编号，本次语音不提交抓取")

    frame = client.freeze_frame(session_id)
    detections = _detect_blocks(frame)
    minimum = mode.get("min_score", 0.5)
    candidates = [d for d in detections if isinstance(d.score, (int, float))
                  and math.isfinite(d.score) and d.score >= minimum
                  and (color is None or d.label in (color, f"block_{color}"))]
    if not candidates:
        log(f"冻结画面中没有合格的{'指定颜色' if color else ''}方块，本次不提交抓取")
        return False
    chosen = max(candidates, key=lambda d: d.score)
    latest = client.mode()
    if (not latest.get("armed") or latest.get("kind") != "block_grasp"
            or latest.get("session_id") != session_id or latest.get("busy")):
        raise BoardError("识别期间抓取会话已变化或开发板正忙，本次语音不提交抓取")
    log(f"提交方块：{chosen.label or '未标注颜色'}，分数 {chosen.score:.3f}，冻结帧 {frame.frame_id}")
    result = client.submit_block(session_id, frame.frame_id, chosen)
    job = client.wait_job(result["job"]["id"])
    status = job.get("status")
    log(f"抓取任务：{status} {job.get('message') or job.get('error') or ''}".rstrip())
    if status != "completed":
        raise BoardError(f"抓取任务未完成：{status}；不会自动重试")
    log("动作流程已完成；实际是否抓牢或放对仍需现场确认")
    return True


def start_expr_recog(client, text, log):
    return _not_implemented("START_EXPR_RECOG", log)


def start_gesture_recog(client, text, log):
    return _not_implemented("START_GESTURE_RECOG", log)


HANDLERS = {
    "MUSIC_DANCE_ROUTINE": music_dance,
    "COURSE_DANCE_ROUTINE": course_dance,
    "POSE_VERTICAL": pose_vertical,
    "POSE_OBSERVE": pose_observe,
    "SET_SERVO_ABS_ANGLE": set_servo_abs_angle,
    "START_BLOCK_GRASP": start_block_grasp,
    "START_EXPR_RECOG": start_expr_recog,
    "START_GESTURE_RECOG": start_gesture_recog,
}


def execute(action, client, text, log=print):
    """按命令标识分发；未注册的配置命令不允许静默执行。"""
    if action not in load_commands():
        raise ValueError(f"分类器返回了未配置的命令：{action}")
    if action == NO_COMMAND:
        return False
    handler = HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"命令缺少执行函数：{action}")
    return handler(client, text, log)
