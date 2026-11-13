"""跳舞动作：由“起始姿态按钮 + 动作模板”生成有限、明确的整数关节目标。纯计算，不连接硬件。

动作模板（data/dances.json 的 pattern）：
  sway   左右摇摆：1 号底座在起始角 ±幅度 间来回
  nod    点头：4 号腕关节在起始角 ±幅度 间来回
  circle 画圈：1 号与 4 号按圆周 8 个点组合运动（末端画一个小圈）
  keyframes 关键帧：模板里逐拍写明绝对目标角（课程舞蹈 course 与音乐舞蹈 music 即用这种方式）。
         要求起始姿态按钮的六个角度与模板的 start_angles 完全相同；超出本机配置范围的值
         必须在模板里人工写成合法值并注明，代码不会自动截断。

起始姿态必须是用户在网页上保存过的六关节姿态按钮（明确目标），不使用实时读数生成目标。
所有目标逐个经过 arm.angle 校验（配置范围、整数），任何一个越界就整套拒绝，不截断、不夹紧。
"""
import copy
import hashlib
import json
import math

import arm

PATTERNS = {'sway': '左右摇摆', 'nod': '点头', 'circle': '画圈', 'keyframes': '关键帧'}
MAX_KEYFRAMES = 60
MAX_AMPLITUDE_DEG = 30
MAX_REPEATS = 5
MAX_STEP_MS = 3000
# 课程资料《任务7：机械臂跳舞》main() 循环体，逐拍照录（time_1=500 ms，time_2=1000 ms）。
# 本机配置范围为1～4、6号10～170°，5号90～250°；原舞第12～14拍有4个值越界，按用户2026-11-06
# 选择在此明确写成边界值：1号0→10、3号180→170、4号0→10、6号180→170。其余全部为原值。
COURSE_KEYFRAMES = [
    {'2': 60, '3': 120, '4': 60}, {'2': 45, '3': 135, '4': 45}, {'2': 60, '3': 120, '4': 60},
    {'2': 90, '3': 90, '4': 90}, {'2': 100, '3': 80, '4': 80}, {'2': 120, '3': 60, '4': 60},
    {'2': 135, '3': 45, '4': 45}, {'2': 90, '3': 90, '4': 90},
    {'4': 20, '6': 150}, {'4': 90, '6': 90}, {'4': 20, '6': 150},
    {'4': 90, '6': 90, '1': 10, '5': 90},
    {'3': 170, '4': 10},
    {'6': 170},
    {'6': 90, 'motion_ms': 1000},
    {'6': 90, '1': 90, '5': 90},
    {'3': 90, '4': 90},
]
# Zeta Education 教程 “Dancing With Music!”（同为 Arm_Lib 接口的 DOFBOT 类机械臂），逐拍照录：
# https://zeta-edu-hgu.readthedocs.io/en/latest/_sources/courses/course_4/12.team_diy/2.sound/2.sound.rst.txt
# 原教程先 write6(0,90,0,180,90,90) 2000 ms，再 7 拍 1600 ms 慢摆、16 拍 600 ms 快甩、收尾 1500/500 ms，共 26 拍。
# 原舞有62个值超出本机配置范围，按用户2026-11-13选择在此明确写成：1～4、6号 0→10、180→170；
# 5号 0→90（本机下限）、180→170。其余为原值。教程页未写许可证，原曲未附带。代码不做任何自动换算。
_UP = {'1': 90, '2': 90, '3': 90, '4': 90, '5': 90, '6': 90}
_BACK = {'1': 90, '2': 90, '3': 10, '4': 170, '5': 90, '6': 90}
_LEFT = {'1': 10, '2': 90, '3': 10, '4': 170, '5': 90, '6': 170}
_RIGHT = {'1': 170, '2': 90, '3': 10, '4': 170, '5': 170, '6': 170}
MUSIC_KEYFRAMES = [
    dict(_BACK, **{'1': 10, '6': 90, 'motion_ms': 2000}),
    dict(_UP, motion_ms=1600), dict(_BACK, **{'1': 170, 'motion_ms': 1600}), dict(_UP, motion_ms=1600),
    dict(_BACK, **{'1': 10, 'motion_ms': 1600}), dict(_UP, motion_ms=1600), dict(_BACK, **{'1': 170, 'motion_ms': 1600}),
    dict(_UP, motion_ms=1600),
    dict(_BACK, **{'4': 90}), dict(_BACK), dict(_LEFT), dict(_BACK), dict(_RIGHT), dict(_BACK),
    dict(_UP), dict(_BACK), dict(_LEFT), dict(_BACK), dict(_RIGHT), dict(_BACK),
    dict(_RIGHT), dict(_BACK), dict(_LEFT), dict(_BACK),
    dict(_BACK, **{'4': 90, 'motion_ms': 1500}), dict(_BACK, **{'4': 90, '6': 170, 'motion_ms': 500}),
]
DEFAULT_DANCES = dict(version=1, routines={
    'music': dict(label='音乐舞蹈', pattern='keyframes', start_angles=[90, 90, 90, 90, 90, 90], repeats=1,
                  motion_ms=600, keyframes=MUSIC_KEYFRAMES,
                  source='Zeta Education 教程 Dancing With Music!（DOFBOT 类机械臂）',
                  adjusted='62处超出本机配置范围的值已改为：1～4、6号 0→10、180→170；5号 0→90、180→170'),
    'course': dict(label='课程舞蹈', pattern='keyframes', start_angles=[90, 90, 90, 90, 90, 90], repeats=1,
                   motion_ms=500, keyframes=COURSE_KEYFRAMES,
                   source='课程资料 02_第二阶段 任务7：机械臂跳舞',
                   adjusted='第12拍1号0→10、第13拍3号180→170与4号0→10、第14拍6号180→170（本机配置范围）'),
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
    raise ValueError('未知动作模板：' + str(pattern))


def keyframe_steps(name, frames, repeats, default_ms):
    """关键帧模板 → [(按关节号排序的[(关节, 绝对角)], 用时)]；整数校验，不做任何换算。"""
    if not isinstance(frames, list) or not 0 < len(frames) <= MAX_KEYFRAMES:
        raise ValueError(f'{name}：keyframes 须为1～{MAX_KEYFRAMES}拍')
    out = []
    for index, frame in enumerate(frames, 1):
        if not isinstance(frame, dict):
            raise ValueError(f'{name} 第{index}拍格式错误')
        ms = _int_in(frame.get('motion_ms', default_ms), 500, MAX_STEP_MS, f'{name} 第{index}拍用时(ms)')
        joints = []
        for key, value in frame.items():
            if key == 'motion_ms':
                continue
            if key not in ('1', '2', '3', '4', '5', '6') or type(value) is not int:
                raise ValueError(f'{name} 第{index}拍：关节号须为"1"～"6"，角度须为整数')
            joints.append((int(key), value))
        if not joints:
            raise ValueError(f'{name} 第{index}拍没有关节目标')
        out.append((sorted(joints), ms))
    return out * repeats


def build_routine(arms, base_state, name, spec):
    """生成一套跳舞计划。base_state 为 saved_states 中已校验的姿态记录。"""
    if not isinstance(spec, dict):
        raise ValueError('动作配置格式错误：' + str(name))
    pattern = spec.get('pattern')
    if pattern not in PATTERNS:
        raise ValueError(f'{name}：pattern 须为 {sorted(PATTERNS)} 之一')
    keyframes = pattern == 'keyframes'
    amplitude = None if keyframes else _int_in(spec.get('amplitude_deg'), 1, MAX_AMPLITUDE_DEG, f'{name} 幅度(度)')
    repeats = _int_in(spec.get('repeats'), 1, MAX_REPEATS, f'{name} 重复次数')
    step_ms = _int_in(spec.get('motion_ms'), 500, MAX_STEP_MS, f'{name} 每步用时(ms)')
    _, settle = arm.timing(dict(arms, motion_ms=step_ms))
    base = list(base_state['angles'])
    if keyframes:
        start = spec.get('start_angles')
        if not isinstance(start, list) or len(start) != 6 or any(type(v) is not int for v in start):
            raise ValueError(f'{name}：start_angles 须为6个整数')
        if base != start:
            raise ValueError(f'需要起始姿态 {start}（例如“竖直”姿态按钮），当前选择的是 {base}')
    steps = []
    # 第 1 步（用户2026-11-13要求）：先回到起始姿态（竖直）。执行时先读六关节，已在目标±1°内的关节跳过；
    # 其余关节全部同批在 300 ms 内到位，须六关节可读且从读数出发的同步路径通过模型碰撞检查，否则不运动、报错停止。
    steps.append(dict(label=f'到起始姿态：{base_state["name"]}',
                      joints=[[j, arm.angle(arms, j, base[j - 1])] for j in range(1, 7)],
                      motion_ms=arm.START_MOVE_MS, settle_ms=settle))
    if keyframes:
        for index, (joints, ms) in enumerate(keyframe_steps(name, spec.get('keyframes'), repeats, step_ms), 1):
            joints = [[j, arm.angle(arms, j, v)] for j, v in joints]
            _, beat_settle = arm.timing(dict(arms, motion_ms=ms))
            steps.append(dict(label=f'{spec.get("label") or name} 第{index}拍', joints=joints, motion_ms=ms, settle_ms=beat_settle))
    else:
        for index, delta in enumerate(offsets(pattern, amplitude, repeats), 1):
            joints = [[j, arm.angle(arms, j, base[j - 1] + d)] for j, d in sorted(delta.items())]
            steps.append(dict(label=f'{PATTERNS[pattern]} 第{index}拍', joints=joints, motion_ms=step_ms, settle_ms=settle))
    plan = dict(name=name, label=spec.get('label') or PATTERNS[pattern], pattern=pattern,
                amplitude_deg=amplitude, repeats=repeats, source=spec.get('source'), adjusted=spec.get('adjusted'), base_state=dict(id=base_state['id'], name=base_state['name'],
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
