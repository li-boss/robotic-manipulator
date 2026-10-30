"""颜色积木演示业务。导入及模拟均不加载 Arm_Lib。"""
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arm_playground"))
import arm


def validate_config(config):
    roi = config.get("roi")
    if roi is not None and (not isinstance(roi, list) or len(roi) != 4
            or any(type(v) is not int for v in roi)
            or not (0 <= roi[0] < roi[2] and 0 <= roi[1] < roi[3])):
        raise ValueError("ROI须为像素坐标 [左, 上, 右, 下]，右下边界不含端点")
    if type(config.get("min_area")) is not int or config["min_area"] < 1:
        raise ValueError("min_area须为正整数")
    if not arm.number(config.get("min_color_fraction")) or not 0.5 <= config["min_color_fraction"] <= 1:
        raise ValueError("颜色占比须为0.5～1")
    colors = config.get("colors")
    if not isinstance(colors, dict) or not colors:
        raise ValueError("至少配置一种颜色")
    for bounds in colors.values():
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("HSV阈值须为两组三个整数")
        low, high = bounds
        if any(not isinstance(row, list) or len(row) != 3 for row in bounds):
            raise ValueError("HSV阈值长度必须为3")
        for i, ceiling in enumerate((179, 255, 255)):
            if any(type(row[i]) is not int for row in bounds) or not 0 <= low[i] <= high[i] <= ceiling:
                raise ValueError("HSV阈值超出范围")
    if type(config.get("vision_reviewed")) is not bool:
        raise ValueError("vision_reviewed须为布尔值")
    if type(config.get("max_detection_age_ms")) is not int or not 100 <= config["max_detection_age_ms"] <= 10000:
        raise ValueError("检测有效期须为100～10000毫秒")
    if not isinstance(config.get("color_targets"), dict) or any(
            color not in colors or not isinstance(target, str) or not target
            for color, target in config["color_targets"].items()):
        raise ValueError("颜色映射必须引用已配置颜色和非空目标名称")
    if not isinstance(config.get("verified_paths"), list) or any(type(v) is not str for v in config["verified_paths"]):
        raise ValueError("verified_paths须为已验证目标名称列表")
    return config


def detect(frame, config, frame_id=0, captured_at=None):
    import cv2
    import numpy as np
    validate_config(config)
    height, width = frame.shape[:2]
    result = dict(request_id=str(uuid.uuid4()), frame_id=frame_id,
                  captured_at=captured_at if captured_at is not None else int(time.time() * 1000),
                  image_width=width, image_height=height, color=None, pixel_x=None, pixel_y=None,
                  result="unconfigured", reason="请设置固定取物区域并确认视觉参数", candidates=[])
    roi = config["roi"]
    if roi is None:
        return result
    left, top, right, bottom = roi
    if right > width or bottom > height:
        return dict(result, result="invalid_roi", reason="取物区域超出当前图像尺寸")
    hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    # 联合所有颜色与未知的有色物体，避免只挑最大已知颜色而漏掉第二块积木。
    union = cv2.inRange(hsv, np.array([0, 60, 40]), np.array([179, 255, 255]))
    masks = {}
    for color, (low, high) in config["colors"].items():
        masks[color] = cv2.inRange(hsv, np.array(low), np.array(high))
        union = cv2.bitwise_or(union, masks[color])
    contours = cv2.findContours(union, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < config["min_area"]:
            continue
        obj = np.zeros(union.shape, dtype=np.uint8)
        cv2.drawContours(obj, [contour], -1, 255, -1)
        count = cv2.countNonZero(obj)
        scores = {color: cv2.countNonZero(cv2.bitwise_and(mask, obj)) / count for color, mask in masks.items()}
        matches = [color for color, score in scores.items() if score >= config["min_color_fraction"]]
        moments = cv2.moments(contour)
        x = left + int(moments["m10"] / moments["m00"])
        y = top + int(moments["m01"] / moments["m00"])
        candidates.append(dict(color=matches[0] if len(matches) == 1 else None,
                               pixel_x=x, pixel_y=y, area=area, scores=scores))
    result["candidates"] = candidates
    if not candidates:
        return dict(result, result="no_target", reason="区域内无足够大的有色目标；不识别灰白或黑色积木")
    if len(candidates) != 1:
        return dict(result, result="multiple_targets", reason="请每次只放一块积木，且保持相互分离")
    candidate = candidates[0]
    if candidate["color"] is None:
        return dict(result, result="unknown_color", reason="颜色未知或阈值有歧义")
    result.update({key: candidate[key] for key in ("color", "pixel_x", "pixel_y")})
    if not config["vision_reviewed"]:
        return dict(result, result="unreviewed", reason="识别仅供调试；尚未确认视觉配置")
    return dict(result, result="detected", reason=None)


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def append(self, event):
        with self.lock:
            arm.append_event(self.path, event)

    def latest(self):
        with self.lock:
            latest, errors = {}, 0
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                        latest[event["task_id"]] = event
                    except (ValueError, KeyError, TypeError):
                        errors += 1
            return list(latest.values()), errors


def task_plan(detection, demo_config, arm_config):
    validate_config(demo_config)
    if detection.get("result") != "detected":
        raise ValueError(detection.get("reason") or "没有可用检测结果")
    age = int(time.time() * 1000) - detection["captured_at"]
    if not 0 <= age <= demo_config["max_detection_age_ms"]:
        raise ValueError("检测结果已过期或时间无效，请重新识别")
    target = demo_config["color_targets"].get(detection["color"])
    if not target:
        raise ValueError("该颜色未配置放置区域")
    steps = arm.plan(arm_config, target)  # 验证全部角度和时间，不补齐任何实机参数。
    duration, settle = arm.timing(arm_config)
    return dict(target=target, motion_ms=duration, settle_ms=settle,
                steps=[dict(label=label, kind=kind, name=name,
                            joints=[[joint, value] for joint, value in enumerate(arm.get_pose(arm_config, name), 1)] if kind == "pose"
                            else [[6, arm.angle(arm_config, 6, arm_config["gripper"][name])]])
                       for label, kind, name in steps])


def simulate(detection, demo_config, arm_config, store):
    # 仅规划，无Controller、无硬件初始化。缺配置也留下可见的拒绝记录。
    with store.lock:
        events, _ = store.latest()
        task_id = detection["request_id"]
        previous = next((row for row in events if row["task_id"] == task_id), None)
        if previous:
            return previous
        event = dict(task_id=task_id, target_id=detection.get("frame_id"), color=detection.get("color"),
                     mode="simulation", started_at=arm.now(), ended_at=arm.now(), retry_count=0,
                     status="completed", result="simulated", failure_reason=None, plan=None)
        try:
            event["plan"] = task_plan(detection, demo_config, arm_config)
        except (ValueError, KeyError) as exc:
            event.update(status="rejected", result="skipped", failure_reason=str(exc))
        store.append(event)
        return event


def statistics(events):
    return dict(total=len(events), simulation=sum(e.get("mode") == "simulation" for e in events),
                success=sum(e.get("mode") == "real" and e.get("result") == "success" for e in events),
                failed=sum(e.get("mode") == "real" and e.get("result") == "failed" for e in events),
                unconfirmed=sum(e.get("mode") == "real" and e.get("result") == "unconfirmed" for e in events))
