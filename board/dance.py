"""跳舞动作：由“起始姿态按钮 + 动作模板”生成有限、明确的整数关节目标。纯计算，不连接硬件。

动作模板（data/dances.json 的 pattern）：
  sway   左右摇摆：1 号底座在起始角 ±幅度 间来回
  nod    点头：4 号腕关节在起始角 ±幅度 间来回
  circle 画圈：1 号与 4 号按圆周 8 个点组合运动（末端画一个小圈）
  snake  蛇形：1～6 号一起按正弦波摆动，相邻关节相位错开 45°，像一条波从底座传到夹爪；
         1、2 号用一半幅度（它们带动整条手臂，摆幅大），其余关节用全幅度

起始姿态必须是用户在网页上保存过的六关节姿态按钮（明确目标），不使用实时读数生成目标。
所有目标逐个经过 arm.angle 校验（配置范围、整数），任何一个越界就整套拒绝，不截断、不夹紧。
"""
import copy
import hashlib
import json
import math

import arm

PATTERNS = {'sway': '左右摇摆', 'nod': '点头', 'circle': '画圈', 'snake': '蛇形'}
SNAKE_WEIGHTS = {1: .5, 2: .5, 3: 1., 4: 1., 5: 1., 6: 1.}
SNAKE_BEATS = 8
MAX_AMPLITUDE_DEG = 30
MAX_REPEATS = 5
MAX_STEP_MS = 3000
DEFAULT_DANCES = dict(version=1, routines={
    'circle': dict(label='画圈', pattern='circle', amplitude_deg=15, repeats=1, motion_ms=600),
    'snake': dict(label='蛇形', pattern='snake', amplitude_deg=20, repeats=2, motion_ms=600),
})


def _int_in(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name}须为{low}～{high}的整数')
    return value


def offsets(pattern, amplitude, repeats):
    """返回每一步相对起始姿态的 {关节: 偏移}；最后一步回到 0 偏移。"""
    if pattern == 'sway':
        out = []
        for _ in range(repeats):
            out += [{1: amplitude}, {1: -amplitude}]
        return out + [{1: 0}]
    if pattern == 'nod':
        out = []
        for _ in range(repeats):
            out += [{4: amplitude}, {4: -amplitude}]
        return out + [{4: 0}]
    if pattern == 'circle':
        # 以起始姿态为圆心：先到圆上 θ=0 点，再按 45° 一拍走完整圈，最后回圆心。
        out = [{1: 0, 4: amplitude}]
        for _ in range(repeats):
            for k in range(1, 9):
                theta = 2 * math.pi * k / 8
                out.append({1: round(amplitude * math.sin(theta)), 4: round(amplitude * math.cos(theta))})
        return out + [{1: 0, 4: 0}]
    if pattern == 'snake':
        # 第k拍：关节j偏移 = 幅度×权重×sin(2πk/8 − (j−1)·45°)；最后一拍全部回到起始姿态。
        def wave(k):
            return {j: round(amplitude * w * math.sin(2 * math.pi * k / SNAKE_BEATS - (j - 1) * math.pi / 4))
                    for j, w in SNAKE_WEIGHTS.items()}
        return [wave(k) for k in range(SNAKE_BEATS * repeats)] + [{j: 0 for j in SNAKE_WEIGHTS}]
    raise ValueError('未知动作模板：' + str(pattern))


def build_routine(arms, base_state, name, spec):
    """生成一套跳舞计划。base_state 为 saved_states 中已校验的姿态记录。"""
    if not isinstance(spec, dict):
        raise ValueError('动作配置格式错误：' + str(name))
    pattern = spec.get('pattern')
    if pattern not in PATTERNS:
        raise ValueError(f'{name}：pattern 须为 {sorted(PATTERNS)} 之一')
    amplitude = _int_in(spec.get('amplitude_deg'), 1, MAX_AMPLITUDE_DEG, f'{name} 幅度(度)')
    repeats = _int_in(spec.get('repeats'), 1, MAX_REPEATS, f'{name} 重复次数')
    step_ms = _int_in(spec.get('motion_ms'), 500, MAX_STEP_MS, f'{name} 每步用时(ms)')
    _, settle = arm.timing(dict(arms, motion_ms=step_ms))
    base = list(base_state['angles'])
    steps = []
    # 第 1 步：按姿态按钮自己的顺序逐关节到起始姿态（与“姿态按钮”执行方式一致）。
    for joint in base_state['order']:
        steps.append(dict(label=f'到起始姿态：{joint}号', joints=[[joint, arm.angle(arms, joint, base[joint - 1])]],
                          motion_ms=base_state['motion_ms'], settle_ms=settle))
    for index, delta in enumerate(offsets(pattern, amplitude, repeats), 1):
        joints = [[j, arm.angle(arms, j, base[j - 1] + d)] for j, d in sorted(delta.items())]
        steps.append(dict(label=f'{PATTERNS[pattern]} 第{index}拍', joints=joints, motion_ms=step_ms, settle_ms=settle))
    plan = dict(name=name, label=spec.get('label') or PATTERNS[pattern], pattern=pattern,
                amplitude_deg=amplitude, repeats=repeats, base_state=dict(id=base_state['id'], name=base_state['name'],
                angles=base), steps=steps, moves=sum(len(s['joints']) for s in steps),
                duration_ms=sum(s['motion_ms'] + s['settle_ms'] for s in steps))
    plan['sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def load_routines(path):
    if not path.exists():
        return copy.deepcopy(DEFAULT_DANCES)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('version') != 1 or not isinstance(data.get('routines'), dict) or not data['routines']:
        raise ValueError('dances.json 格式异常：需要 version=1 和非空 routines')
    return data
