"""从语音转写文字里取出“几号舵机”和“多少度”。只做文字解析，不判断角度是否合法（由开发板按配置范围校验）。

支持阿拉伯数字和中文数字（零～二百七十，含“两”“幺”），例如：
    一号舵机转到90度、把六号舵机转成七十度、第三关节设定在115度、5号舵机旋到二百度
听不清或有歧义（多个舵机、多个角度、小数）时抛 ValueError，不猜测。
"""
import re

_DIGITS = {'零': 0, '〇': 0, '幺': 1, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9}
_NUMBER = r'[0-9零〇幺一二两三四五六七八九十百]+'
# “N号”“第N”“N舵机/关节”任一写法都算提到一个舵机，用于发现一句话里有多个舵机。
_JOINT = re.compile(r'第\s*(' + _NUMBER + r')|(' + _NUMBER + r')\s*(?:号|舵机|关节|电机)')
_ANGLE = re.compile(r'(' + _NUMBER + r')\s*(?:[点.．]\s*[0-9零一二三四五六七八九]+\s*)?度')
_DECIMAL = re.compile(_NUMBER + r'\s*[点.．]\s*[0-9零一二三四五六七八九]+\s*度')


def chinese_number(text):
    """'135' / '一百三十五' / '一三五' / '九十' / '十五' / '两百' → int；无法解析时抛 ValueError。"""
    if text.isdigit():
        return int(text)
    if any(c not in _DIGITS and c not in '十百' for c in text) or not text:
        raise ValueError(f'无法识别的数字：{text}')
    if '十' not in text and '百' not in text:          # 逐位读法：一三五
        return int(''.join(str(_DIGITS[c]) for c in text))
    total, rest = 0, text
    if '百' in rest:
        head, _, rest = rest.partition('百')
        if len(head) != 1 or head not in _DIGITS:
            raise ValueError(f'无法识别的数字：{text}')
        total += _DIGITS[head] * 100
        if len(rest) == 1 and rest in _DIGITS and rest != '零':   # 一百五 = 150
            return total + _DIGITS[rest] * 10
        rest = rest.lstrip('零〇')
    if '十' in rest:
        head, _, tail = rest.partition('十')
        if len(head) > 1 or (head and head not in _DIGITS):
            raise ValueError(f'无法识别的数字：{text}')
        total += (_DIGITS[head] if head else 1) * 10
        rest = tail
    if rest:
        if len(rest) != 1 or rest not in _DIGITS:
            raise ValueError(f'无法识别的数字：{text}')
        total += _DIGITS[rest]
    return total


def parse_servo_command(text):
    """返回 (舵机编号, 目标角度)；都必须恰好出现一次。"""
    if not isinstance(text, str):
        raise ValueError('没有识别文字')
    compact = re.sub(r'\s+', '', text)
    if _DECIMAL.search(compact):
        raise ValueError('角度必须是整数，不接受小数')
    joints = {chinese_number(a or b) for a, b in _JOINT.findall(compact)}
    if len(joints) != 1:
        raise ValueError('没听清是几号舵机' if not joints else '一句话里出现了多个舵机，请一次只说一个')
    angles = {chinese_number(m) for m in _ANGLE.findall(compact)}
    if len(angles) != 1:
        raise ValueError('没听清要转到多少度' if not angles else '一句话里出现了多个角度，请只说一个')
    joint, angle = joints.pop(), angles.pop()
    if not 1 <= joint <= 6:
        raise ValueError(f'舵机编号须为 1～6，听到的是 {joint}')
    return joint, angle
