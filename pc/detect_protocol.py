"""局域网方块识别协议 jjt-detect/1 的校验（Mac 端与识别服务共用）。只用标准库，不连接任何设备。

协议说明见 docs/方块识别局域网接口.md。识别服务只能返回“图中方块的像素位置”，
不允许任何额外字段（例如放行、跳过检查一类的开关）；出现即整次拒绝。
"""
import math
import re

from armlink import BlockDetection

PROTOCOL = 'jjt-detect/1'
TOP_KEYS = {'protocol', 'frame_id', 'model', 'ms', 'detections'}
DETECTION_KEYS = {'corners', 'bbox', 'angle_deg', 'center', 'score', 'label'}
MAX_DETECTIONS = 20
FRAME_ID = re.compile(r'^[0-9a-f]{32}$')


class ProtocolError(ValueError):
    """识别服务的回复不符合协议。"""


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f'{name} 须为有限数值')
    return float(value)


def _point(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProtocolError(f'{name} 须为 [u, v]')
    u, v = _number(value[0], name + '.u'), _number(value[1], name + '.v')
    if not (0 <= u < size[0] and 0 <= v < size[1]):
        raise ProtocolError(f'{name} ({u:.1f}, {v:.1f}) 不在原图 {size[0]}×{size[1]} 内')
    return u, v


def parse_detection(item, size, index=0):
    """一个检测的字典 → BlockDetection；任何不合规都抛 ProtocolError。"""
    name = f'detections[{index}]'
    if not isinstance(item, dict):
        raise ProtocolError(name + ' 须为对象')
    extra = set(item) - DETECTION_KEYS
    if extra:
        raise ProtocolError(f'{name} 含协议外字段 {sorted(extra)}，整次拒绝')
    score = _number(item.get('score'), name + '.score')
    if not 0 <= score <= 1:
        raise ProtocolError(name + '.score 须在 0～1')
    label = item.get('label')
    if label is not None and (not isinstance(label, str) or len(label) > 64):
        raise ProtocolError(name + '.label 须为不超过64字的字符串')
    center = _point(item['center'], size, name + '.center') if item.get('center') is not None else None
    corners, bbox, angle = item.get('corners'), item.get('bbox'), item.get('angle_deg')
    if (corners is None) == (bbox is None):
        raise ProtocolError(name + ' 须且只能有 corners 或 bbox 之一')
    if corners is not None:
        if angle is not None:
            raise ProtocolError(name + '.angle_deg 只能与 bbox 一起使用')
        if not isinstance(corners, list) or len(corners) != 4:
            raise ProtocolError(name + '.corners 须为顶面4个角点')
        corners = [_point(c, size, f'{name}.corners[{i}]') for i, c in enumerate(corners)]
        return BlockDetection(corners=corners, center=center, score=score, label=label)
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ProtocolError(name + '.bbox 须为 [left, top, right, bottom]')
    left, top, right, bottom = (_number(v, name + '.bbox') for v in bbox)
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        raise ProtocolError(name + '.bbox 不在原图内或左上右下顺序不对')
    angle = None if angle is None else _number(angle, name + '.angle_deg')
    return BlockDetection(bbox=[left, top, right, bottom], angle_deg=angle, center=center, score=score, label=label)


def parse_reply(reply, frame_id, size):
    """整份回复 → [BlockDetection]；frame_id 必须原样带回。"""
    if not isinstance(reply, dict):
        raise ProtocolError('回复须为 JSON 对象')
    extra = set(reply) - TOP_KEYS
    if extra:
        raise ProtocolError(f'回复含协议外字段 {sorted(extra)}，整次拒绝')
    if reply.get('protocol') != PROTOCOL:
        raise ProtocolError(f'协议版本不符：{reply.get("protocol")!r}（需要 {PROTOCOL}）')
    if reply.get('frame_id') != frame_id:
        raise ProtocolError('回复的 frame_id 与本帧不符')
    model = reply.get('model')
    if model is not None and (not isinstance(model, str) or len(model) > 200):
        raise ProtocolError('model 须为不超过200字的字符串')
    if reply.get('ms') is not None:
        _number(reply['ms'], 'ms')
    items = reply.get('detections')
    if not isinstance(items, list) or len(items) > MAX_DETECTIONS:
        raise ProtocolError(f'detections 须为最多 {MAX_DETECTIONS} 项的列表')
    return [parse_detection(item, size, i) for i, item in enumerate(items)]


def check_frame_id(value):
    if not isinstance(value, str) or not FRAME_ID.match(value):
        raise ProtocolError('frame_id 须为32位小写十六进制')
    return value
