"""局域网表情手势识别协议 jjt-gesture/1 的校验（Mac 端与识别服务共用）。只用标准库，不连接任何设备。

协议说明见 docs/表情手势局域网接口.md。识别服务只能返回“这一帧是什么表情/手势”，
不允许任何额外字段（例如动作名、关节角度、跳过检查一类的开关）；出现即整次拒绝。
跳什么舞由 Mac 端的映射决定，开发板再按网页开启的会话授权执行。
"""
import math

from armlink import GestureEvent
from detect_protocol import ProtocolError, check_frame_id  # noqa: F401  （与方块识别共用）

PROTOCOL = 'jjt-gesture/1'
TOP_KEYS = {'protocol', 'frame_id', 'model', 'ms', 'event'}
EVENT_KEYS = {'label', 'confidence'}


def parse_event(item):
    if item is None:
        return None
    if not isinstance(item, dict):
        raise ProtocolError('event 须为对象或 null')
    extra = set(item) - EVENT_KEYS
    if extra:
        raise ProtocolError(f'event 含协议外字段 {sorted(extra)}，整次拒绝')
    label = item.get('label')
    if not isinstance(label, str) or not label.strip() or len(label) > 64:
        raise ProtocolError('event.label 须为1～64字的字符串')
    confidence = item.get('confidence')
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ProtocolError('event.confidence 须为0～1的有限数值')
    return GestureEvent(label=label, confidence=float(confidence))


def parse_reply(reply, frame_id):
    """整份回复 → GestureEvent 或 None；frame_id 必须原样带回。"""
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
    ms = reply.get('ms')
    if ms is not None and (isinstance(ms, bool) or not isinstance(ms, (int, float)) or not math.isfinite(ms)):
        raise ProtocolError('ms 须为有限数值')
    if 'event' not in reply:
        raise ProtocolError('回复缺少 event（没识别到时给 null）')
    return parse_event(reply['event'])
