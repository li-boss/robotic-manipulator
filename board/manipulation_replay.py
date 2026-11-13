"""开发板复核电脑端计划：按电脑给出的指令组逐段重放检查，不做候选搜索。

电脑端只提供各阶段的关节指令组（`steps[*].joints`）。开发板用自己的配置、标定、
九点参考和模型重新计算定位、起点、夹持附着、放置补偿、衔接切角和整份计划字段；
每个阶段的终点按与候选求解相同的位置/方向/接近/高度条件检查，每个指令组按
与同步下发相同的线性插值及发送滞后包络做碰撞扫掠检查。任何一项不符即拒绝，
由调用方改用开发板本地完整规划。复核不连接硬件、不发送运动。
"""
import math
import multiprocessing
import os

import numpy as np

import arm
import cad_geometry
from manipulation_guided import GuidedSearch
from manipulation_reference import reference_seed

GRIP_LABELS = ('夹持方块', '释放方块')
FIRST_LABEL = '张开夹爪并到取物上方'


def _int(value):
    return type(value) is int


def remote_groups(steps):
    """校验电脑端步骤的基本结构；返回[(标签, 指令组)]。"""
    if not isinstance(steps, list) or not 0 < len(steps) <= 200:
        raise ValueError('电脑端计划步骤数无效')
    rows = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('label'), str):
            raise ValueError('电脑端计划步骤格式无效')
        joints = step.get('joints')
        if not isinstance(joints, list) or not joints:
            raise ValueError('电脑端计划指令组为空')
        seen = set()
        for pair in joints:
            if (not isinstance(pair, list) or len(pair) != 2 or not all(map(_int, pair))
                    or not 1 <= pair[0] <= 6 or pair[0] in seen):
                raise ValueError('电脑端计划关节指令无效')
            seen.add(pair[0])
        rows.append((step['label'], [list(p) for p in joints]))
    return rows


class ReplaySearch(GuidedSearch):
    """与GuidedSearch接口相同；move()只核对电脑端该阶段的指令组。"""

    def __init__(self, cfg, arms, reference, steps):
        super().__init__(cfg, arms, reference)
        self.remote = remote_groups(steps)
        self.index = 0
        # 扫掠样本先记录、后统一（可并行）检查；结果与当场逐段检查相同。
        self.jobs = []
        self.blends = 0
        robot = self.robot
        original_blend = robot.blend_check

        def deferred_samples(values, carried=None, margin=0):
            self.jobs.append(dict(kind=self._job_kind, label=self._job_label,
                                  values=np.asarray(values, float), carried=carried, margin=margin))

        def deferred_blend(start, middle, group, carried=None):
            self._job_kind, self._job_label = 'blend', self.blends
            self.blends += 1
            try:
                original_blend(start, middle, group, carried)
            finally:
                self._job_kind, self._job_label = 'required', None

        self._job_kind, self._job_label = 'required', None
        robot.collision_samples = deferred_samples
        robot.blend_check = deferred_blend

    def _stage(self, label):
        i = self.index
        while i < len(self.remote) and self.remote[i][0] in GRIP_LABELS:
            i += 1
        groups = []
        while i < len(self.remote):
            name, joints = self.remote[i]
            if name == FIRST_LABEL and i == 0:
                name, joints = '到取物上方', [p for p in joints if p[0] != 6]
            if name != label:
                break
            if any(p[0] == 6 for p in joints):
                raise ValueError(label + '：移动指令组不能包含夹爪')
            groups.append(joints)
            i += 1
        if not groups:
            raise ValueError('电脑端计划缺少阶段：' + label)
        return groups, i

    def _sweep(self, state, group, carried, margin):
        q0 = np.asarray(state, float)
        q1 = q0.copy()
        for joint, value in group:
            arm.angle(self.robot.arm, joint, value)
            q1[joint - 1] = value
        if np.array_equal(q0, q1):
            raise ValueError('电脑端指令组没有实际变化')
        self._job_label = self._stage_label
        count = max(1, int(math.ceil(max(abs(q1 - q0)) / .25)))
        self.robot.collision_samples(q0 + np.outer(np.linspace(0, 1, count + 1), q1 - q0), carried, margin)
        order = [joint - 1 for joint, _ in group]
        if len(order) > 1:
            # 与sync_transition相同：按下发顺序第k个关节晚k个发送间隔起步。
            lag = self.robot.SEND_GAP_MS / arm.timing(self.robot.arm)[0]
            time_axis = np.linspace(0, 1 + lag * (len(order) - 1), count + len(order))
            lagged = np.repeat(q0[None], len(time_axis), axis=0)
            for rank, joint in enumerate(order):
                lagged[:, joint] = q0[joint] + np.clip(time_axis - rank * lag, 0, 1) * (q1[joint] - q0[joint])
            self.robot.collision_samples(lagged, carried, margin)
        if cad_geometry.dynamic_capture(self.cfg):
            self.robot.fk([int(v) for v in q1], True)
        return [int(v) for v in q1]

    def move(self, label, xyz, current, yaw, carried, held_wrist=None, tilt_hint=None, force_tilt=False):
        groups, after = self._stage(label)
        self._stage_label = label
        stage = 'grasp' if label in ('接近抓取', '下降放置') else 'above'
        reference_xy = self.located['destination_xy_mm'] if label == '下降放置' else xyz[:2]
        source = reference_seed(self.reference, reference_xy, stage)
        margin = self.robot._cad_reach * math.radians(1.25)
        self.robot.collision(current, carried, margin)
        state = list(current)
        for group in groups:
            state = self._sweep(state, group, carried, margin)
        end = state
        self.robot.collision(end, carried, margin)
        if held_wrist is not None and end[4] != held_wrist:
            raise ValueError(label + '：腕部未保持夹持时角度')
        pose, _ = self.robot.fk(end)
        error = float(np.linalg.norm(pose[:3, 3] * 1000 - np.asarray(xyz, float)))
        if error > self.cfg['max_position_error_mm']:
            raise ValueError(f'{label}：终点位置误差{error:.2f}毫米超过允许值')
        jaw = (pose[:3, :3] @ self.robot.jaw_axis)[:2]
        if np.linalg.norm(jaw) < 1e-8:
            raise ValueError(label + '：夹爪方向投影退化')
        jaw = jaw / np.linalg.norm(jaw)
        direction = min(math.degrees(math.acos(float(np.clip(jaw @ [math.cos(math.radians(yaw + k)), math.sin(math.radians(yaw + k))], -1, 1))))
                        for k in (0, 90, 180, 270))
        if direction > self.cfg['max_direction_error_deg']:
            raise ValueError(f'{label}：夹爪方向误差{direction:.2f}°超过允许值')
        radial = np.asarray(xyz[:2], float) / 1000
        radial = radial / max(np.linalg.norm(radial), 1e-12)
        tilts = self.tilts(source['angles'], tilt_hint, force_tilt)
        approach = [np.r_[radial * math.sin(math.radians(t)), -math.cos(math.radians(t))] for t in tilts]
        if not any(np.linalg.norm(pose[:3, 2] - a) <= .035 for a in approach):
            raise ValueError(label + '：接近方向不在允许候选内')
        if label == '接近抓取':
            z = self.cfg['table_z_mm']
            if not z < pose[2, 3] * 1000 < z + self.cfg['block_size_mm'][2]:
                raise ValueError('接近抓取：模型夹持中心不在方块高度内')
        actual_tilt = math.degrees(math.acos(float(np.clip(-pose[2, 2], -1, 1))))
        self.index = after
        self.stages.append(dict(label=label, source=source, tilt_deg=actual_tilt, requested_tilt_deg=actual_tilt,
                                angles=end, position_error_mm=error))
        return groups, end, actual_tilt


CHUNK_ROWS = 64
_SHARED = None


def _check(task):
    """子进程任务：检查一段扫掠样本；返回None或失败原因。"""
    from manipulation_collision import check_samples
    robot, jobs = _SHARED
    index, start, stop = task
    job = jobs[index]
    try:
        check_samples(robot, job['values'][start:stop], job['carried'], job['margin'])
    except ValueError as exc:
        return str(exc)
    return None


def workers():
    try:
        count = int(os.environ.get('JJT_VERIFY_WORKERS', '0'))
    except ValueError:
        count = 0
    if count <= 0:
        count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    return max(1, min(count, 3))


def run_jobs(robot, jobs, processes=None):
    """检查全部记录的扫掠；返回每个任务的失败原因（None为通过）。

    样本逐行独立判定，按行切块后分到多个进程，结果与整段检查相同。
    仅在支持fork的系统（开发板Linux）上并行，否则顺序执行。"""
    global _SHARED
    from manipulation_collision import check_samples
    tasks = [(i, start, min(start + CHUNK_ROWS, len(job['values'])))
             for i, job in enumerate(jobs) for start in range(0, len(job['values']), CHUNK_ROWS)]
    processes = workers() if processes is None else processes
    failures = [None] * len(jobs)
    original = type(robot).collision_samples
    robot.collision_samples = original.__get__(robot)
    if processes > 1 and len(tasks) > 1 and 'fork' in multiprocessing.get_all_start_methods():
        _SHARED = (robot, jobs)
        try:
            with multiprocessing.get_context('fork').Pool(processes) as pool:
                results = pool.map(_check, tasks, chunksize=1)
        finally:
            _SHARED = None
        for (index, _, _), error in zip(tasks, results):
            if error is not None and failures[index] is None:
                failures[index] = error
        return failures
    for i, job in enumerate(jobs):
        try:
            check_samples(robot, job['values'], job['carried'], job['margin'])
        except ValueError as exc:
            failures[i] = str(exc)
    return failures


def verify_remote(cfg, arms, target, require_path, reference, steps, processes=None):
    """返回开发板重新组装并检查过的计划；电脑端计划与之不一致时抛错。"""
    from manipulation_geometry import _build_plan, locate, mark_continuous, readiness
    if reference is None:
        raise ValueError('电脑端计划只用于九点引导模式')
    ready = readiness(cfg, arms, require_path)
    if not ready['ready']:
        raise ValueError('；'.join(x['reason'] for x in ready['issues']))
    located = locate(cfg, target)
    search = ReplaySearch(cfg, arms, reference, steps)
    search.located = located
    search.begin([])
    plan = _build_plan(cfg, arms, target, require_path, 0, None, guided=search)
    if [(s['label'], s['joints']) for s in plan['steps']] != search.remote:
        raise ValueError('电脑端计划的阶段或夹爪指令与开发板重放结果不一致')
    failures = run_jobs(search.robot, search.jobs, processes)
    for job, error in zip(search.jobs, failures):
        if error is not None and job['kind'] == 'required':
            raise ValueError(f'{job["label"]}：同步扫掠未通过：{error}')
    # 衔接切角：与_build_plan相同，检查不通过的位置改回停稳回读。
    blended = [s for s in plan['steps'] if s.get('blend_next_ms')]
    if len(blended) != search.blends:
        raise ValueError('衔接检查记录与计划不符')
    rejected = {job['label'] for job, error in zip(search.jobs, failures) if job['kind'] == 'blend' and error is not None}
    for k, step in enumerate(blended):
        if k in rejected:
            del step['blend_next_ms']
    mark_continuous(plan)   # 衔接被拒的位置改为连续下发（随臂计划）或停稳回读，并重算用时
    plan['reference'] = search.summary()
    return plan
