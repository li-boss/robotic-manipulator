"""电脑端识别模块的接入点：自动方块识别抓取模式、表情/手势跳舞模式。

授权方式（AGENTS.md“网页开启模式授权”一节）：
  * 用户在 /modes 页面先“预览”，看到本次会话的全部限制（任务类型、放置点、最多次数、
    有效期；跳舞则是每套动作的全部关节目标与用时），再点“开启模式”——这一次点击就是
    对本会话内有限次数动作的授权。
  * 会话开启前，电脑端的运动请求一律拒绝；会话超时、用完次数、任一运动失败、用户关闭
    或电脑端请求停止，会话立即结束。
  * 每次抓取仍走原抓放流程：冻结画面与真实读数 → 定位 → 完整路径检查 → 硬件锁/版本/去重 →
    Controller.write。电脑端只提交“图像里方块在哪”或“要跳哪一套”，从不提交关节角度。
"""
import hashlib
import json
import math
import secrets
import threading
import time

import arm
from dance import PATTERNS, build_routine, load_routines
from paths import BOARD, WEB
from saved_states import checked as checked_state

API_VERSION = 1
KINDS = {'block_grasp': '方块识别自动抓取', 'gesture_dance': '表情手势跳舞'}
MAX_CONSECUTIVE_PLAN_FAILURES = 5


def _int(fields, key, default, low, high, name):
    value = fields.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name}须为{low}～{high}的整数')
    return value


def _hex32(value, name):
    if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'{name}须为32位小写十六进制字符串')
    return value


def detection_to_target(detection, image_size, frame_id, task_mode, destination, block_size_id):
    """把电脑端的方块检测结果转换成抓放模块的统一目标结构（source='vision'）。

    detection 两种写法（像素坐标，原图分辨率，原点左上角）：
      1) corners: 顶面四个角，沿轮廓顺序（顺/逆时针均可）；第1→第2角那条边作为夹取方向参考。
         可选 center: 顶面中心；省略时取两条对角线交点。（推荐，旋转/透视都准）
      2) bbox: [left, top, right, bottom]，可选 angle_deg: 顶面某条边在图像中的方向（度，
         0=水平向右，顺时针为正）；省略按 0。可选 center；省略取框中心。
    """
    if not isinstance(detection, dict):
        raise ValueError('detection 必须是对象')

    def point(value, name):
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not all(arm.number(v) for v in value)):
            raise ValueError(name + '须为[u, v]两个有限数值')
        return [float(value[0]), float(value[1])]

    corners = detection.get('corners')
    if corners is not None:
        if not isinstance(corners, list) or len(corners) != 4:
            raise ValueError('corners 须为顶面4个角点')
        corners = [point(c, f'第{i + 1}个角点') for i, c in enumerate(corners)]
        if detection.get('center') is not None:
            center = point(detection['center'], 'center')
        else:
            (x1, y1), (x2, y2), (x3, y3), (x4, y4) = corners
            den = (x1 - x3) * (y2 - y4) - (y1 - y3) * (x2 - x4)
            if abs(den) < 1e-9:
                raise ValueError('四个角点退化，无法求顶面中心')
            t = ((x1 - x2) * (y2 - y4) - (y1 - y2) * (x2 - x4)) / den
            center = [x1 + t * (x3 - x1), y1 + t * (y3 - y1)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        bbox = [min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2]
        edge = [corners[0], corners[1]]
    elif detection.get('bbox') is not None:
        box = detection['bbox']
        if not isinstance(box, list) or len(box) != 4 or not all(arm.number(v) for v in box):
            raise ValueError('bbox 须为[left, top, right, bottom]')
        left, top, right, bottom = map(float, box)
        if right - left < 4 or bottom - top < 4:
            raise ValueError('bbox 过小')
        center = point(detection['center'], 'center') if detection.get('center') is not None else [(left + right) / 2, (top + bottom) / 2]
        angle = detection.get('angle_deg', 0)
        if not arm.number(angle):
            raise ValueError('angle_deg 须为有限数值')
        half = min(right - left, bottom - top) / 4
        dx, dy = half * math.cos(math.radians(angle)), half * math.sin(math.radians(angle))
        edge = [[center[0] - dx, center[1] - dy], [center[0] + dx, center[1] + dy]]
        bbox = [left, top, right, bottom]
    else:
        raise ValueError('detection 需要 corners 或 bbox')
    w, h = image_size
    bbox = [max(0.0, bbox[0]), max(0.0, bbox[1]), min(w - 1.0, bbox[2]), min(h - 1.0, bbox[3])]
    target = dict(source='vision', task_mode=task_mode, frame_id=frame_id, image_size=list(image_size),
                  block_size_id=block_size_id, bbox=bbox, center_px=center, edge_px=edge)
    if corners is not None:
        target['outline_px'] = corners
    if task_mode == 'pick_place':
        target['destination'] = dict(kind='fixed', name=destination)
    return target


class Modes:
    def __init__(self, demo):
        self.demo, self.control, self.manipulation = demo, demo.control, demo.manipulation
        self.dances_path = demo.data_dir / 'dances.json'
        self.session = None
        self.offer = None
        self.history = []
        self.control.modes = self
        self.collision_cache = {}
        self.collision_lock = threading.Lock()
        self.web_stop = None
        self.control.extra_audit_paths.extend([BOARD / 'modes.py', BOARD / 'dance.py', WEB / 'modes.html', WEB / 'modes_ui.js'])
        if self.dances_path.exists():
            self.control.extra_audit_paths.append(self.dances_path)

    # ------------------------------------------------------------------ 网页
    def status(self):
        with self.control.lock:
            self._sync()
            routines = load_routines(self.dances_path)['routines']
            cfg = self.manipulation.load()
            return dict(kinds=KINDS, patterns=PATTERNS, routines=routines, session=self._public(),
                        destinations=sorted((cfg.get('destinations') or {}).keys()),
                        saved_states=self.control.states.status()['states'],
                        mode='real' if self.control.real else 'simulation', history=self.history[-10:])

    def _plan(self, fields):
        kind = fields.get('kind')
        if kind not in KINDS:
            raise ValueError('未知模式')
        expires_min = _int(fields, 'expires_minutes', 10, 1, 60, '有效期(分钟)')
        arms = self.control.config()
        if self.control.real and arms.get('hardware_ready') is not True:
            raise ValueError('实机未就绪（arm_config.json 的 hardware_ready 不是 true），拒绝开启')
        if kind == 'block_grasp':
            task_mode = fields.get('task_mode', 'pick_place')
            if task_mode not in ('pick_place', 'pickup'):
                raise ValueError('任务类型须为 pick_place 或 pickup')
            cfg = self.manipulation.load()
            destination = fields.get('destination')
            if task_mode == 'pick_place' and destination not in (cfg.get('destinations') or {}):
                raise ValueError('请选择一个已保存的固定放置点（在框选抓放页面保存）')
            # 只夹取并抬升结束时保持夹持，无法连续抓第二块。
            max_actions = 1 if task_mode == 'pickup' else _int(fields, 'max_actions', 3, 1, 20, '最多抓取次数')
            score = fields.get('min_score', 0.5)
            if not arm.number(score) or not 0 <= score <= 1:
                raise ValueError('min_score 须为0～1')
            ready = self.manipulation.status()['execution_readiness']
            if not ready['ready']:
                raise ValueError('抓放配置未就绪：' + '；'.join(i['reason'] for i in ready['issues']))
            return dict(kind=kind, task_mode=task_mode, destination=destination if task_mode == 'pick_place' else None,
                        destination_xy_mm=(cfg['destinations'][destination] if task_mode == 'pick_place' else None),
                        max_actions=max_actions, min_score=float(score), expires_minutes=expires_min,
                        block_size_id=cfg['block_size_id'],
                        conditions='每次由电脑端识别结果在新冻结画面上定位，并完整检查路径后自动执行；'
                                   '桌面只放待抓方块，放置点保持空；任一步失败立即结束会话，不重试、不复位、不松爪。')
        states = self.control.states.status()['states']
        state = next((s for s in states if s['id'] == fields.get('base_state_id')), None)
        if state is None:
            raise ValueError('请选择一个已保存的姿态按钮作为跳舞起始姿态')
        base = dict(checked_state(arms, state), id=state['id'])
        available = load_routines(self.dances_path)['routines']
        names = fields.get('routines') or sorted(available)
        if not isinstance(names, list) or not names or any(n not in available for n in names):
            raise ValueError('跳舞动作须从 dances.json 的 routines 中选择')
        routines = {}
        for n in names:
            try:
                routines[n] = build_routine(arms, base, n, available[n])
            except ValueError as exc:
                raise ValueError(f'动作“{n}”从起始姿态“{base["name"]}”出发不能执行：{exc}；请换起始姿态或修改 dances.json') from None
            check = self.check_collision(routines[n])
            if check['checked'] and not check['ok']:
                raise ValueError(f'动作“{n}”从起始姿态“{base["name"]}”出发：{check["error"]}')
        return dict(kind=kind, base_state=base, routines=routines,
                    max_actions=_int(fields, 'max_actions', 10, 1, 30, '最多跳舞次数'),
                    cooldown_s=_int(fields, 'cooldown_s', 3, 0, 60, '两次之间最短间隔(秒)'),
                    expires_minutes=expires_min,
                    conditions='每套动作先按姿态按钮顺序逐关节到起始姿态，再按列出的整数目标摆动；'
                               '运动中不回读，周围留出摆动空间；失败立即结束会话，不重试、不复位、不松爪。')

    def preview(self, fields):
        """生成本次会话的完整参数供用户核对；不运动。"""
        with self.control.lock:
            self.control.idle()
            plan = self._plan(fields)
            audit = self.control.audit(dict(operation='mode_preview', kind=plan['kind'], moves=0))
            self.offer = dict(token=secrets.token_urlsafe(24), expires=time.monotonic() + 120, plan=plan, audit=audit,
                              fields={k: v for k, v in fields.items() if k != 'csrf'})
            return dict(token=self.offer['token'], expires_in=120, plan=plan,
                        mode='real' if self.control.real else 'simulation')

    def arm(self, fields):
        """用户点击“开启模式”：授权本会话内的有限次数动作。"""
        with self.control.lock:
            offer = self.offer
            if not offer or fields.get('token') != offer['token'] or fields.get('confirmed') is not True:
                raise ValueError('请先预览本次模式参数并勾选确认')
            self.offer = None
            if time.monotonic() > offer['expires']:
                raise ValueError('预览已过期，请重新预览')
            plan = self._plan(offer['fields'])
            if plan != offer['plan']:
                raise ValueError('预览后配置或姿态已变化，请重新预览')
            audit = self.control.audit(dict(operation='mode_arm', kind=plan['kind'], moves=0))
            if audit['hashes'] != offer['audit']['hashes']:
                raise ValueError('规则、代码或配置已变化，请重新预览')
            now = time.time()
            self.session = dict(id=secrets.token_hex(16), kind=plan['kind'], plan=plan, audit_hashes=audit['hashes'],
                                armed_at=now, expires_at=now + 60 * plan['expires_minutes'], used=0,
                                last_action_at=0, plan_failures=0, jobs=[], status='armed', message='等待电脑端请求')
            self.control.record('mode_armed', dict(session=self.session['id'], plan=plan))
            return dict(session=self._public())

    def disarm(self, fields=None, source='web', reason=None):
        with self.control.lock:
            if self.session is None:
                return dict(session=None, message='当前没有开启的模式')
            if source == 'pc' and (fields or {}).get('session_id') not in (None, self.session['id']):
                raise ValueError('会话编号不符')
            ended = self.session
            self.session = None
            ended.update(status='ended', ended_at=time.time(),
                         message=reason or ('网页关闭模式' if source == 'web' else '电脑端请求停止'))
            self.history.append({k: ended[k] for k in ('id', 'kind', 'used', 'status', 'message')})
            self.control.record('mode_ended', dict(session=ended['id'], used=ended['used'], reason=ended['message']))
            return dict(session=None, message=ended['message'] + '；已下发的运动无法撤回，不是硬件急停')

    # ------------------------------------------------------------------ 网页跳舞预设按钮
    def check_collision(self, routine):
        """用抓放的CAD碰撞模型检查：起始姿态 → 每一拍（同批下发线性插值 + 20 ms发送滞后包络）。

        到起始姿态的逐关节步骤从当前未知姿态出发，与姿态按钮相同，不在检查范围内。
        结果按动作、模型与配置缓存。模型为CAD估计，未实测；不代表实机轨迹已验证。"""
        cfg = self.manipulation.load()
        if cfg.get('cad') is None:
            return dict(checked=False, ok=None, note='未配置CAD碰撞模型，没有做碰撞检查')
        from manipulation_geometry import ManipulationRobot, calibration_version
        arms = self.control.config()
        key = hashlib.sha256(json.dumps([routine['sha256'], calibration_version(cfg), arms], sort_keys=True).encode()).hexdigest()
        with self.collision_lock:
            if key in self.collision_cache:
                return self.collision_cache[key]
            started = time.monotonic()
            robot = ManipulationRobot(cfg, arms)
            margin = robot._cad_reach * math.radians(1.25)
            lag = robot.SEND_GAP_MS / max(1, min(s['motion_ms'] for s in routine['steps']))
            state = [float(v) for v in routine['base_state']['angles'][:5]]
            samples = 0
            result = None
            beats = [s for s in routine['steps'] if not s['label'].startswith('到起始姿态')]
            try:
                robot.collision(state, None, margin)
                for index, step in enumerate(beats, 1):
                    order = [j - 1 for j, _ in step['joints'] if j <= 5]
                    q0 = list(state)
                    for j, v in step['joints']:
                        if j <= 5:
                            state[j - 1] = float(v)
                    q0, q1 = [float(v) for v in q0], [float(v) for v in state]
                    span = max(abs(a - b) for a, b in zip(q0, q1))
                    if span == 0:
                        continue
                    count = max(1, int(math.ceil(span / .25)))
                    rows = [[a + (b - a) * n / count for a, b in zip(q0, q1)] for n in range(count + 1)]
                    total = 1 + lag * max(len(order) - 1, 0)
                    for n in range(count + len(order)):
                        t = total * n / max(1, count + len(order) - 1)
                        rows.append([q0[j] + min(max(t - order.index(j) * lag, 0), 1) * (q1[j] - q0[j]) if j in order else q0[j]
                                     for j in range(5)])
                    samples += len(rows)
                    try:
                        robot.collision_samples(rows, None, margin)
                    except ValueError as exc:
                        raise ValueError(f'第{index}拍（{step["label"]}）模型碰撞检查未通过：{exc}') from None
                result = dict(checked=True, ok=True, samples=samples, note='CAD模型碰撞检查通过（模型估计，未实测）')
            except ValueError as exc:
                result = dict(checked=True, ok=False, samples=samples, error=str(exc))
            result['seconds'] = round(time.monotonic() - started, 2)
            if len(self.collision_cache) > 64:
                self.collision_cache.clear()
            self.collision_cache[key] = result
            return result

    def _web_routine(self, fields):
        name = fields.get('routine')
        available = load_routines(self.dances_path)['routines']
        if name not in available:
            raise ValueError('未知跳舞预设：' + str(name))
        states = self.control.states.status()
        state = next((x for x in states['states'] if x['id'] == fields.get('state_id')), None)
        if state is None:
            raise ValueError('请选择一个已保存的姿态按钮作为跳舞起始姿态')
        arms = self.control.config()
        base = dict(checked_state(arms, state), id=state['id'])
        try:
            routine = build_routine(arms, base, name, available[name])
        except ValueError as exc:
            raise ValueError(f'“{available[name].get("label", name)}”从起始姿态“{base["name"]}”出发不能执行：{exc}；请换起始姿态或修改 dances.json') from None
        return routine, states['sha256']

    def dance_presets(self):
        with self.control.lock:
            return dict(routines=load_routines(self.dances_path)['routines'], patterns=PATTERNS,
                        mode='real' if self.control.real else 'simulation')

    def dance_preview(self, fields):
        """生成网页跳舞按钮显示的完整计划与模型碰撞检查；不运动。"""
        with self.control.lock:
            routine, states_sha = self._web_routine(fields)
        check = self.check_collision(routine)
        return dict(routine=routine, states_sha256=states_sha, collision=check,
                    mode='real' if self.control.real else 'simulation')

    def dance_start(self, fields):
        """用户点击卡片上的“开始跳舞”：即本次授权卡片列出的这一套动作。"""
        request_id = _hex32(fields.get('request_id'), 'request_id')
        if fields.get('confirmed') is not True:
            raise ValueError('请点击显示本次全部参数的跳舞按钮')
        with self.control.lock:
            routine, states_sha = self._web_routine(fields)
        check = self.check_collision(routine)
        with self.control.lock:
            if request_id in self.control.direct_jobs:
                job = self.control.direct_jobs[request_id]
                if job['parameters'].get('routine_sha256') != fields.get('routine_sha256'):
                    raise ValueError('请求编号已经绑定其他参数')
                return dict(job=job, duplicate=True)
            again, again_sha = self._web_routine(fields)
            if again != routine or again_sha != states_sha or states_sha != fields.get('states_sha256'):
                raise ValueError('姿态按钮或跳舞配置已变化，请刷新页面重新核对')
            if routine['sha256'] != fields.get('routine_sha256'):
                raise ValueError('按钮显示的动作与当前配置不同，请刷新页面重新核对')
            if check['checked'] and not check['ok']:
                raise ValueError(check['error'])
            self._sync()
            if self.session is not None:
                raise ValueError('识别模式正在开启中，请先在 /modes 关闭模式再用按钮跳舞')
            self.control.idle()
            arms = self.control.config()
            if self.control.real and arms.get('hardware_ready') is not True:
                raise ValueError('实机未就绪，拒绝运动')
            args = dict(operation='dance', session=None, source='web_button', routine=routine['name'],
                        routine_sha256=routine['sha256'], states_sha256=states_sha, collision=check,
                        steps=routine['steps'], moves=len(routine['steps']))
            audit = self.control.audit(args)
            self.web_stop = None
            result = self.control._start_direct(request_id, args, audit)
            return dict(job={k: result['job'].get(k) for k in ('id', 'status', 'total')}, routine=routine['name'],
                        duration_ms=routine['duration_ms'])

    def dance_stop(self, fields):
        """停止网页按钮跳舞的后续拍子；已下发的那一拍无法撤回。"""
        with self.control.lock:
            job = self.control.job
            if not (self.control.busy and job and job['parameters'].get('operation') == 'dance'
                    and job['parameters'].get('session') is None):
                return dict(stopped=False, message='当前没有正在进行的按钮跳舞')
            self.web_stop = job['id']
            return dict(stopped=True, message='已请求停止：当前这一拍结束后不再下发；已发出的指令无法撤回')

    # ------------------------------------------------------------------ 内部
    def _public(self):
        s = self.session
        if s is None:
            return None
        out = {k: s[k] for k in ('id', 'kind', 'used', 'status', 'message', 'jobs')}
        out.update(max_actions=s['plan']['max_actions'], expires_in=max(0, int(s['expires_at'] - time.time())),
                   plan=s['plan'])
        return out

    def _sync(self):
        """检查会话是否过期、次数用完或上一个动作失败；需要时结束会话。"""
        s = self.session
        if s is None:
            return
        job = self.control.job
        if job and job.get('id') in s['jobs'] and job.get('status') in ('failed', 'unknown'):
            self.disarm(reason='动作执行失败，会话已结束：' + str(job.get('error') or job.get('message')))
        elif time.time() > s['expires_at']:
            self.disarm(reason='会话已到有效期')
        elif s['used'] >= s['plan']['max_actions'] and not self.control.busy:
            self.disarm(reason='已用完本次会话的动作次数')

    def _session(self, fields, kind):
        self._sync()
        s = self.session
        if s is None:
            raise ValueError('模式未开启：请先在网页 /modes 上开启' + KINDS[kind])
        if s['kind'] != kind:
            raise ValueError('当前开启的是' + KINDS[s['kind']] + '，不是' + KINDS[kind])
        if fields.get('session_id') != s['id']:
            raise ValueError('会话编号不符（会话可能已重开），请重新读取 /api/v1/mode')
        if s['used'] >= s['plan']['max_actions']:
            raise ValueError('本会话动作次数已用完')
        audit = self.control.audit(dict(operation='mode_check', kind=kind, moves=0))
        # 冻结画面等新证据文件会加入审计清单；只比较开启时已存在的规则/代码/配置文件。
        if any(audit['hashes'].get(path) != digest for path, digest in s['audit_hashes'].items()):
            self.disarm(reason='规则、代码或配置在会话期间变化')
            raise ValueError('规则、代码或配置在会话期间变化，会话已结束')
        return s

    # ------------------------------------------------------------------ 电脑端 API
    def api_health(self):
        return dict(service='mechanical-arm-board', api_version=API_VERSION,
                    mode='real' if self.control.real else 'simulation', camera=self.demo.camera.status(),
                    busy=self.control.busy)

    def api_mode(self):
        with self.control.lock:
            self._sync()
            s = self.session
            if s is None:
                return dict(armed=False, kind=None, session_id=None, busy=self.control.busy)
            public = self._public()
            out = dict(armed=True, kind=s['kind'], session_id=s['id'], used=s['used'], max_actions=public['max_actions'],
                       expires_in=public['expires_in'], busy=self.control.busy, message=s['message'])
            if s['kind'] == 'gesture_dance':
                out.update(routines=sorted(s['plan']['routines']), cooldown_s=s['plan']['cooldown_s'])
            else:
                out.update(task_mode=s['plan']['task_mode'], min_score=s['plan']['min_score'])
            return out

    def api_job(self):
        with self.control.lock:
            self._sync()
            job = self.control.job
            if not job:
                return dict(job=None, busy=self.control.busy)
            return dict(busy=self.control.busy, job={k: job.get(k) for k in
                        ('id', 'status', 'sent', 'total', 'message', 'error', 'note')})

    def api_snapshot(self):
        """最新一帧 JPEG（不冻结、不读舵机），供电脑端预览或用开发板摄像头做手势识别。"""
        with self.demo.camera.condition:
            if not self.demo.camera.status()['connected']:
                raise ValueError('摄像头未连接')
            return bytes(self.demo.camera.jpeg)

    def api_block_frame(self, fields):
        """冻结一帧：读取六关节真实读数 + 当前画面，返回给电脑端识别。不运动。"""
        with self.control.lock:
            self._session(fields, 'block_grasp')
        captured = self.manipulation.capture({})
        return {k: captured[k] for k in ('id', 'image_size', 'captured_at', 'joints', 'jpeg_base64')} | dict(frame_id=captured['id'])

    def api_block_target(self, fields):
        """提交识别结果：定位 → 完整规划 → 自动执行（会话授权）。返回本次任务编号。"""
        with self.control.lock:
            s = self._session(fields, 'block_grasp')
            plan = s['plan']
            frame_id = _hex32(fields.get('frame_id'), 'frame_id')
            detection = fields.get('detection')
            score = (detection or {}).get('score', 1.0)
            if not arm.number(score) or score < plan['min_score']:
                raise ValueError(f'检测置信度{score!r}低于本会话下限{plan["min_score"]}，未规划')
            meta = self.manipulation.evidence(frame_id)
            target = detection_to_target(detection, meta['image_size'], frame_id, plan['task_mode'],
                                         plan['destination'], plan['block_size_id'])
        try:
            preview = self.manipulation.preview(dict(target=target))
        except Exception as exc:
            with self.control.lock:
                if self.session is s:
                    s['plan_failures'] += 1
                    s['message'] = '规划未通过：' + str(exc)
                    if s['plan_failures'] >= MAX_CONSECUTIVE_PLAN_FAILURES:
                        self.disarm(reason=f'连续{MAX_CONSECUTIVE_PLAN_FAILURES}次规划未通过')
            raise
        with self.control.lock:
            if self.session is not s:
                self.control.pending = None
                raise ValueError('规划期间会话已结束，未执行')
            self._session(fields, 'block_grasp')
            token = preview['token']
            self.control.record('mode_execute', dict(session=s['id'], token=token, kind='block_grasp',
                                                     detection=detection, target=target))
            result = self.control.execute(dict(token=token, confirmed=True))
            s['used'] += 1
            s['plan_failures'] = 0
            s['jobs'].append(token)
            s['last_action_at'] = time.time()
            s['message'] = f'第{s["used"]}次抓取已开始执行'
            steps = preview['parameters']['plan']['steps']
            return dict(job=result['job'] and {k: result['job'].get(k) for k in ('id', 'status')},
                        target=preview['parameters']['plan']['target'], steps=len(steps),
                        warnings=preview['parameters']['plan'].get('warnings', []), used=s['used'])

    def api_gesture(self, fields):
        """提交一次手势/表情触发：执行会话内已确认的一套跳舞动作。"""
        with self.control.lock:
            s = self._session(fields, 'gesture_dance')
            request_id = _hex32(fields.get('request_id'), 'request_id')
            if request_id in self.control.direct_jobs:
                return dict(job=self.control.direct_jobs[request_id], duplicate=True)
            name = fields.get('routine')
            if name not in s['plan']['routines']:
                raise ValueError('本会话未授权该跳舞动作：' + str(name))
            wait = s['plan']['cooldown_s'] - (time.time() - s['last_action_at'])
            if wait > 0:
                raise ValueError(f'距上一次跳舞不足{s["plan"]["cooldown_s"]}秒，请{wait:.1f}秒后再试')
            self.control.idle()
            arms = self.control.config()
            state = next((x for x in self.control.states.status()['states'] if x['id'] == s['plan']['base_state']['id']), None)
            if state is None:
                raise ValueError('起始姿态按钮已删除，请重新开启模式')
            routine = build_routine(arms, dict(checked_state(arms, state), id=state['id']), name,
                                    load_routines(self.dances_path)['routines'].get(name))
            if routine != s['plan']['routines'][name]:
                raise ValueError('跳舞动作或起始姿态在会话期间改变，未执行；请重新开启模式')
            trigger = dict(label=fields.get('label'), confidence=fields.get('confidence'))
            args = dict(operation='dance', session=s['id'], routine=name, routine_sha256=routine['sha256'],
                        steps=routine['steps'], moves=len(routine['steps']), trigger=trigger)
            audit = self.control.audit(args)
            if self.control.real and arms.get('hardware_ready') is not True:
                raise ValueError('实机未就绪，拒绝运动')
            result = self.control._start_direct(request_id, args, audit)
            s['used'] += 1
            s['jobs'].append(request_id)
            s['last_action_at'] = time.time()
            s['message'] = f'第{s["used"]}次跳舞：{routine["label"]}（{trigger["label"]}）'
            return dict(job={k: result['job'].get(k) for k in ('id', 'status', 'total')}, routine=name,
                        duration_ms=routine['duration_ms'], used=s['used'])

    def run_dance(self, controller, p):
        """在 Control 的任务线程里执行（已持硬件锁）。逐步批量写入，异常即停。"""
        args = p['parameters']
        steps = args['steps']
        base_cfg = controller.config
        for step in steps:   # 整套先校验，任何目标非法都不下发第一条
            for joint, value in step['joints']:
                arm.angle(base_cfg, joint, value)
            arm.timing(dict(base_cfg, motion_ms=step['motion_ms'], settle_ms=step['settle_ms']))
        for index, step in enumerate(steps):
            with self.control.lock:
                if args.get('session') is None:
                    if self.web_stop == p['token']:
                        return dict(status='stopped', message=f'已按网页请求停止后续动作（已下发{index}/{len(steps)}步）')
                elif self.session is None or self.session['id'] != args['session']:
                    return dict(status='stopped', message=f'模式已关闭，停止后续动作（已下发{index}/{len(steps)}步）')
            if self.control.audit(args)['hashes'] != p['audit']['hashes']:
                raise ValueError('执行期间规则、代码或配置变化，停止后续动作')
            controller.config = dict(base_cfg, motion_ms=step['motion_ms'], settle_ms=step['settle_ms'])
            self.control.record('sending', dict(id=p['token'], step=index, joints=step['joints'], operation='dance'))
            try:
                controller.write([tuple(pair) for pair in step['joints']], retries=arm.MAX_WRITE_RETRIES)
            finally:
                for retry in getattr(controller, 'last_retries', []):
                    self.control.record('write_retry', dict(id=p['token'], step=index, **retry))
            with self.control.lock:
                for joint, value in step['joints']:
                    self.control.commanded[str(joint)] = value
                self.control.rows = []
                self.control.job.update(sent=index + 1, total=len(steps), message=f'{step["label"]}（{index + 1}/{len(steps)}）')
        return dict(status='completed', message='跳舞动作已全部下发；运动中未回读，请看现场')
