"""网页单关节及明确六关节姿态；无启动运动、无自动重试。"""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time

from core import arm
from pose_setup import POSE_NAMES, required, save_config
from saved_states import StateStore, checked as checked_state

ROOT = Path(__file__).resolve().parent


class Control:
    def __init__(self, config_path, real=False, factory=arm.Controller, root=ROOT.parent,
                 lock_factory=arm.hardware_lock):
        self.path, self.root = Path(config_path), Path(root)
        self.real, self.factory, self.lock_factory = real, factory, lock_factory
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.pending = None
        self.job = None
        self.busy = False
        self.rows = []
        self.commanded = {}
        self.log = self.path.parent / 'logs/web_control.jsonl'
        self.extra_audit_paths = []
        self.states = StateStore(self.path.parent / 'manual_states.json')
        self.state_analysis = None
        if self.states.path.exists():
            self.extra_audit_paths.append(self.states.path)
        # 已接收编号持久化在审计日志中；重启或网络重发不再次下发。
        self.direct_jobs = {}
        self.manipulation_jobs = {}
        if self.log.exists():
            for line in self.log.read_text(encoding='utf-8').splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get('kind') == 'accepted' and entry.get('parameters', {}).get('operation') == 'manipulation':
                    self.manipulation_jobs[entry['id']] = dict(id=entry['id'], status='unknown',
                        message='此前已接收，重启后不重复下发；请现场核对')
                elif entry.get('kind') == 'finished' and entry.get('id') in self.manipulation_jobs:
                    self.manipulation_jobs[entry['id']].update(entry)
                if entry.get('kind') == 'accepted' and entry.get('parameters', {}).get('operation') in ('direct_move', 'state_move'):
                    self.direct_jobs[entry['id']] = dict(id=entry['id'], parameters=entry['parameters'],
                        status='unknown', message='此前已接收，不重复下发；请现场核对')
                elif entry.get('kind') == 'finished' and entry.get('id') in self.direct_jobs:
                    self.direct_jobs[entry['id']].update(entry)

    def config(self):
        return json.loads(self.path.read_text(encoding='utf-8-sig'))

    def audit(self, parameters):
        paths = [self.root / 'AGENTS.md', self.root / '机械臂操作准则.md',
                 ROOT / 'control.py', ROOT / 'server.py', ROOT / 'index.html',
                 ROOT / 'core.py', ROOT / 'start_service.py',
                 ROOT / 'saved_states.py', ROOT / 'state_ui.js',
                 Path(arm.__file__), self.root / 'arm_playground/pose_setup.py',
                 self.root / 'arm_playground/joint_check.py', self.root / 'camera_preview/server.py', self.path]
        hashes = {}
        for p in paths + self.extra_audit_paths:
            data = p.read_bytes()
            if not data:
                raise ValueError('规则、代码或配置为空：' + str(p))
            hashes[str(p)] = hashlib.sha256(data).hexdigest()
        result = dict(hashes=hashes, parameters=parameters, mode='real' if self.real else 'simulation',
                      checked_at=arm.now(), message='统一校验；逐关节下发；异常不重试、不复位、不松爪')
        self.record('audit', result)
        return result

    def record(self, kind, data):
        arm.append_event(self.log, {**data, 'kind': kind, 'at': arm.now(),
                                  'mode': 'real' if self.real else 'simulation'})

    def idle(self):
        if self.busy:
            raise ValueError('有操作正在执行，请查询状态，勿重发')

    def hardware(self):
        return self.lock_factory() if self.real else nullcontext()

    def read_rows(self, config, allow_failed=False):
        rows = self.factory(config, real=self.real).read_report()
        self.rows = rows
        self.record('read', dict(joints=rows))
        if len(rows) != 6 or (not allow_failed and not all(r['read_ok'] for r in rows)):
            raise ValueError('六关节读取不完整，禁止执行或保存；未重试')
        for i, row in enumerate(rows, 1):
            if row['id'] != i:
                raise ValueError('关节读取顺序异常')
            if allow_failed and row['read_ok'] is False:
                continue
            arm.observed_angle(config, i, row['angle'])
        if allow_failed and any(r['read_ok'] is False for r in rows):
            self.record('read_warning', dict(joints=rows, policy='warn_continue',
                                            message='读取失败，仅记录警告并继续本次已确认抓取；未重读或补造读数'))
        return rows

    def status(self):
        with self.lock:
            cfg = self.config()
            return dict(mode='real' if self.real else 'simulation', csrf=self.csrf, busy=self.busy,
                        joints=self.rows, commanded=self.commanded, job=self.job,
                        joint_limits=cfg.get('joint_limits', {}), missing=required(cfg),
                        poses=cfg.get('poses'), gripper=cfg.get('gripper'), targets=cfg.get('targets'), saved_states=self.states.status())

    def read(self):
        with self.lock:
            self.idle()
            self.pending = None
            audit = self.audit(dict(operation='read'))
            with self.hardware():
                self.read_rows(self.config())
            return dict(status=self.status(), audit=audit)

    def preview(self, fields):
        with self.lock:
            self.idle()
            self.pending = None
            operation = fields.get('operation')
            original = self.path.read_bytes()
            cfg = json.loads(original.decode('utf-8-sig'))
            if operation == 'move':
                joint = fields.get('joint')
                target = arm.angle(cfg, joint, fields.get('angle'))
                cfg = dict(cfg, motion_ms=fields.get('motion_ms'))
                duration, settle = arm.timing(cfg)
                if self.real and cfg.get('hardware_ready') is not True:
                    raise ValueError('实机未就绪，拒绝运动')
                parameters = dict(operation=operation, joint=joint, angle=target,
                                  motion_ms=duration, settle_ms=settle, moves=1)
            elif operation == 'save':
                if not self.real:
                    raise ValueError('模拟读数不能保存为实机姿态')
                name, values = fields.get('name'), fields.get('angles')
                if name not in POSE_NAMES:
                    raise ValueError('未知姿态名称')
                ids = [6] if name.startswith('gripper.') else list(range(1, 6))
                if not isinstance(values, list) or len(values) != len(ids):
                    raise ValueError('请明确填写姿态五个目标或夹爪一个目标')
                values = [arm.angle(cfg, i, v) for i, v in zip(ids, values)]
                parameters = dict(operation=operation, name=name, angles=values)
            else:
                raise ValueError('未知操作')
            audit = self.audit(parameters)
            if audit['hashes'][str(self.path)] != hashlib.sha256(original).hexdigest():
                raise ValueError('配置在预览期间改变，请重新预览')
            with self.hardware():
                rows = self.read_rows(cfg)
            token = secrets.token_urlsafe(24)
            self.pending = dict(token=token, expires=time.monotonic()+120, audit=audit,
                                parameters=parameters, rows=rows, config_bytes=original)
            return dict(token=token, expires_in=120, audit=audit, parameters=parameters, joints=rows,
                        mode='real' if self.real else 'simulation',
                        conditions='核对现场路径、线缆、手指和负载；仅一个控制来源；掌握设备停止方式。页面不是硬件急停。保存姿态不代表路径已验证。')

    def execute(self, fields):
        with self.lock:
            token = fields.get('token')
            if not isinstance(token,str) or not token or len(token)>256:
                raise ValueError('无效的一次性预览编号')
            if token in self.manipulation_jobs:
                return dict(job=self.manipulation_jobs[token])
            if self.job and token == self.job['id']:
                return dict(job=self.job)  # 网络重发只查询，绝不重新执行。
            self.idle()
            p = self.pending
            if not p or token != p['token'] or fields.get('confirmed') is not True:
                raise ValueError('需要本次预览和明确确认')
            self.pending = None
            if time.monotonic() > p['expires']:
                raise ValueError('预览已过期，请重新预览')
            audit = self.audit(p['parameters'])
            if audit['hashes'] != p['audit']['hashes']:
                raise ValueError('规则、代码或配置变化，请重新预览')
            if p['parameters']['operation']=='auto':
                self.auto_task.validate_execution(p)
            if p['parameters']['operation']=='teaching':
                self.teaching.validate_execution(p)
            if p['parameters']['operation']=='manipulation':
                self.manipulation.validate_execution(p)
            self.record('accepted', dict(id=token, parameters=p['parameters']))
            self.busy = True
            self.job = dict(id=token, status='running', parameters=p['parameters'], audit=audit)
            if p['parameters']['operation']=='manipulation':
                self.manipulation_jobs[token]=self.job
            try:
                threading.Thread(target=self._work, args=(p,), daemon=True).start()
            except Exception as exc:
                self.busy = False
                self.job.update(status='failed', error=str(exc))
                raise
            return dict(job=self.job)

    def analyze_state(self, fields):
        with self.lock:
            self.idle()
            self.state_analysis = None
            original = self.path.read_bytes()
            cfg = json.loads(original.decode('utf-8-sig'))
            record = checked_state(cfg, fields)
            current = self.states.status()
            if current['sha256'] != fields.get('expected_sha256'):
                raise ValueError('姿态列表已变化，请刷新')
            identifier = fields.get('id')
            if identifier is not None and not any(s['id'] == identifier for s in current['states']):
                raise ValueError('编辑的姿态不存在')
            duration, settle = arm.timing(dict(cfg, motion_ms=record['motion_ms']))
            audit = self.audit(dict(operation='analyze_state', **record, moves=0))
            if audit['hashes'][str(self.path)] != hashlib.sha256(original).hexdigest():
                raise ValueError('配置在分析期间变化，请重新分析')
            token = secrets.token_urlsafe(24)
            self.state_analysis = dict(token=token, expires=time.monotonic()+120, audit=audit,
                record=record, expected=current['sha256'], id=identifier)
            return dict(token=token, record=record, audit=audit, steps=[[i, record['angles'][i-1]] for i in record['order']],
                duration_ms=6*(duration+settle), message='六关节目标和顺序检查通过；未运动，未验证现场路径')

    def save_state(self, fields):
        with self.lock:
            self.idle()
            p = self.state_analysis
            if not p or fields.get('token') != p['token'] or time.monotonic() > p['expires']:
                raise ValueError('先分析本次参数，再保存；分析有效期120秒')
            self.state_analysis = None
            audit = self.audit(dict(operation='save_state', **p['record'], moves=0))
            if audit['hashes'] != p['audit']['hashes']:
                raise ValueError('规则、代码或配置变化，请重新分析')
            with self.hardware():
                result = self.states.save(p['record'], p['expected'], p['id'])
            if self.states.path not in self.extra_audit_paths:
                self.extra_audit_paths.append(self.states.path)
            self.pending = None
            self.record('saved_state', dict(**result, parameters=p['record']))
            return dict(**result, audit=audit)

    def delete_state(self, fields):
        with self.lock:
            self.idle()
            audit = self.audit(dict(operation='delete_state', id=fields.get('id'), moves=0))
            with self.hardware():
                result = self.states.save(None, fields.get('expected_sha256'), fields.get('id'), delete=True)
            self.state_analysis = None
            self.pending = None
            return dict(**result, audit=audit)

    def state_move(self, fields):
        with self.lock:
            token = fields.get('request_id')
            if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                raise ValueError('需要32位单次请求编号')
            if fields.get('confirmed') is not True:
                raise ValueError('请点击显示本次全部参数的姿态按钮')
            current = self.states.status()
            if current['sha256'] != fields.get('expected_sha256'):
                raise ValueError('姿态已变化，请刷新按钮后再点击')
            state = next((s for s in current['states'] if s['id'] == fields.get('id')), None)
            if state is None:
                raise ValueError('姿态不存在')
            original = self.path.read_bytes()
            cfg = json.loads(original.decode('utf-8-sig'))
            record = checked_state(cfg, state)
            if fields.get('record') != record:
                raise ValueError('按钮显示参数与保存参数不同，请刷新')
            duration, settle = arm.timing(dict(cfg, motion_ms=record['motion_ms']))
            args = dict(operation='state_move', **record, state_id=state['id'],
                state_sha256=current['sha256'], settle_ms=settle, moves=6)
            if token in self.direct_jobs:
                if self.direct_jobs[token]['parameters'] != args:
                    raise ValueError('请求编号已经绑定其他参数')
                return dict(job=self.direct_jobs[token])
            self.idle()
            if self.real and cfg.get('hardware_ready') is not True:
                raise ValueError('实机未就绪，拒绝运动')
            audit = self.audit(args)
            if audit['hashes'][str(self.path)] != hashlib.sha256(original).hexdigest() or self.states.status()['sha256'] != current['sha256']:
                raise ValueError('配置或姿态在提交期间变化，未执行')
            return self._start_direct(token, args, audit)

    def _start_direct(self, token, args, audit):
        self.pending = None
        self.record('accepted', dict(id=token, parameters=args))
        self.busy = True
        self.job = dict(id=token, status='running', parameters=args, audit=audit, sent=0, total=args['moves'])
        self.direct_jobs[token] = self.job
        try:
            threading.Thread(target=self._work, args=(dict(token=token, parameters=args, audit=audit),), daemon=True).start()
        except Exception as exc:
            self.busy = False
            self.job.update(status='failed', error=str(exc))
            self.record('finished', dict(id=token, status='failed', error=str(exc)))
            raise
        return dict(job=self.job)

    def direct_move(self, fields):
        """回车/确定即本次授权；不要求读取任一关节，无自动重试。"""
        with self.lock:
            token = fields.get('request_id')
            if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                raise ValueError('需要32位单次请求编号')
            if fields.get('confirmed') is not True:
                raise ValueError('需要通过回车或确定授权本次调整')
            original = self.path.read_bytes()
            cfg = json.loads(original.decode('utf-8-sig'))
            joint = fields.get('joint')
            target = arm.angle(cfg, joint, fields.get('angle'))
            duration, settle = arm.timing(dict(cfg, motion_ms=fields.get('motion_ms')))
            args = dict(operation='direct_move', joint=joint, angle=target,
                        motion_ms=duration, settle_ms=settle, moves=1)
            if token in self.direct_jobs:
                old = self.direct_jobs[token]
                if old['parameters'] != args:
                    raise ValueError('请求编号已经绑定其他参数')
                return dict(job=old)
            self.idle()
            if self.real and cfg.get('hardware_ready') is not True:
                raise ValueError('实机未就绪，拒绝运动')
            audit = self.audit(args)
            if audit['hashes'][str(self.path)] != hashlib.sha256(original).hexdigest():
                raise ValueError('配置在提交期间变化，请重新操作')
            self.pending = None
            self.record('accepted', dict(id=token, parameters=args))
            self.busy = True
            self.job = dict(id=token, status='running', parameters=args, audit=audit)
            self.direct_jobs[token] = self.job
            try:
                threading.Thread(target=self._work, args=(dict(token=token, parameters=args, audit=audit),), daemon=True).start()
            except Exception as exc:
                self.busy = False
                self.job.update(status='failed', error=str(exc))
                self.record('finished', dict(id=token, status='failed', error=str(exc)))
                raise
            return dict(job=self.job)

    def _work(self, p):
        outcome = {}
        try:
            with self.hardware():
                audit = self.audit(p['parameters'])
                if audit['hashes'] != p['audit']['hashes']:
                    raise ValueError('规则、代码或配置变化，未执行')
                cfg = self.config()
                args = p['parameters']
                if args['operation'] in ('direct_move', 'state_move'):
                    cfg = dict(cfg, motion_ms=args['motion_ms'])
                    pairs = ([(i, args['angles'][i-1]) for i in args['order']] if args['operation'] == 'state_move'
                             else [(args['joint'], args['angle'])])
                    # 整批先校验，任何目标非法都不能部分下发。
                    pairs = [(i, arm.angle(cfg, i, value)) for i, value in pairs]
                    arm.timing(cfg)
                    controller = self.factory(cfg, real=self.real)
                    for index, (joint, value) in enumerate(pairs):
                        if self.audit(args)['hashes'] != p['audit']['hashes']:
                            raise ValueError('规则、代码或配置变化，停止后续关节')
                        self.record('sending', dict(id=p['token'], parameters=args, step=index, joint=joint, angle=value))
                        # 用户10-30：写入失败重发同一绝对目标最多2次，逐次记录。
                        try:
                            controller.write([(joint, value)], retries=arm.MAX_WRITE_RETRIES)
                        finally:
                            for retry in getattr(controller, 'last_retries', []):
                                self.record('write_retry', dict(id=p['token'], step=index, **retry))
                        with self.lock:
                            self.commanded[str(joint)] = value
                            self.rows = []
                            self.job.update(sent=index+1, total=len(pairs), message=f'已下发{index+1}/{len(pairs)}个关节')
                    outcome = dict(status='completed', message='本次目标已依序下发；未读取关节，请看现场结果')
                    return
                teaching = args['operation']=='teaching' and self.teaching.continue_read_failures(args)
                manipulation = args['operation']=='manipulation' and self.manipulation.continue_read_failures(args)
                rows = self.read_rows(cfg, allow_failed=teaching or manipulation)
                if teaching:
                    self.teaching.observed(self.teaching.load(), rows, allow_failed=True)
                # 随臂抓放与预览一致性同为1°；其他入口保持读数完全一致。
                allowed = 1 if manipulation else 0
                if any(r['read_ok'] and old['read_ok'] and abs(r['angle']-old['angle'])>allowed
                       for r, old in zip(rows, p['rows'])):
                    raise ValueError('起始读数变化，请重新预览并确认；未运动或保存')
                if manipulation:
                    p['rows'] = rows
                if teaching:
                    p['read_warnings'] = self.teaching.read_warnings(p['rows'], 'preview') + self.teaching.read_warnings(rows, 'startup')
                    p['rows'] = rows
                if 'teaching_step' in args:
                    self.teaching.validate_step(p,rows)
                if args['operation'] == 'move':
                    cfg = dict(cfg, motion_ms=args['motion_ms'])
                    controller = self.factory(cfg, real=self.real)
                    self.record('sending', dict(id=p['token'], parameters=args))
                    controller.write([(args['joint'], args['angle'])])
                    with self.lock:
                        self.commanded[str(args['joint'])] = args['angle']
                    after=self.read_rows(cfg)
                    if 'teaching_step' in args:
                        expected=[r['angle'] for r in rows];expected[args['joint']-1]=args['angle']
                        if any(abs(r['angle']-v)>1 for r,v in zip(after,expected)):
                            raise ValueError('验证步骤回读或其他关节偏差超过1°，停止后续步骤')
                        self.teaching.step_finished(p,after)
                    outcome = dict(status='completed', message='单次下发完成，请现场确认结果；未自动纠正读数')
                elif args['operation']=='auto':
                    controller=self.factory(cfg,real=self.real)
                    outcome=self.auto_task.run(controller,p)
                elif args['operation']=='teaching':
                    outcome=self.teaching.run(self.factory(cfg,real=self.real),p)
                elif args['operation']=='manipulation':
                    outcome=self.manipulation.run(self.factory(cfg,real=self.real),p)
                else:
                    name = args['name']
                    if name.startswith('gripper.'):
                        cfg.setdefault('gripper', {})[name.split('.')[1]] = args['angles'][0]
                    elif name.startswith('targets.'):
                        _, color, kind = name.split('.')
                        cfg.setdefault('targets', {}).setdefault(color, {})[kind] = args['angles']
                    else:
                        cfg.setdefault('poses', {})[name] = args['angles']
                    backup = save_config(self.path, cfg, p['config_bytes'])
                    outcome = dict(status='completed', backup=str(backup), message='姿态已保存，未运动；路径仍需验证')
        except Exception as exc:
            if 'teaching_step' in p['parameters']:
                try:self.teaching.step_finished(p,[],exc)
                except Exception:pass
            outcome = dict(status='failed', error=str(exc), message='停止后续操作；未重试、复位或松爪')
        finally:
            try:
                self.record('finished', dict(id=p['token'], **outcome))
            except Exception as exc:
                outcome = dict(status='failed', error='结果日志写入失败：'+str(exc))
            with self.lock:
                self.job.update(outcome)
                self.busy = False
