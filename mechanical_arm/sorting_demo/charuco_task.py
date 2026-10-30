"""Audited calibration sessions. Captures/readbacks only; no movement entry point."""
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import cv2
import numpy as np

from auto_task import AutoTask
from auto_plan import arm_fingerprint
import cad_geometry
import charuco_geometry as geometry
from manipulation_geometry import checked_snapshot, project_to_plane

ROOT = Path(__file__).resolve().parent
# This exact predecessor differs only in hand-eye consistency acceptance and
# result metadata. Its intrinsic solver is unchanged; keep certified optics.
INTRINSICS_COMPATIBLE_SOLVERS = frozenset({
    '2d7441047b4d5be9b269a7a623ad1131c41886bcfda15e398c0163a5b7cd3ca0',
})


class Calibration:
    def __init__(self, manipulation):
        self.manipulation = manipulation
        self.demo, self.control = manipulation.demo, manipulation.control
        self.path = manipulation.path.parent/'charuco_calibration.json'
        self.frames = manipulation.path.parent/'logs/calibration/frames'
        self.runtime = ROOT.parent/'calibration_runtime/site-packages'
        for name in ('charuco_task.py', 'charuco_geometry.py', 'charuco_worker.py', 'calibration.html', 'calibration_ui.js'):
            self.register(ROOT/name)
        if self.path.exists():
            self.register(self.path)

    def register(self, path):
        if path not in self.control.extra_audit_paths:
            self.control.extra_audit_paths.append(path)

    def load(self):
        if not self.path.exists():
            return dict(version=1, revision=0, sessions={}, captures={}, solutions={})
        return json.loads(self.path.read_text(encoding='utf-8'))

    def guard(self, fields):
        self.control.idle()
        data = self.load()
        if type(fields.get('revision')) is not int or fields['revision'] != data['revision']:
            raise ValueError('标定资料已改变，请刷新后再操作')
        self.control.pending = None
        return data

    def store(self, data, operation):
        self.control.audit(dict(operation='calibration_'+operation, moves=0))
        data['revision'] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backup = self.frames.parent/'history'
            backup.mkdir(parents=True, exist_ok=True)
            (backup/('state_'+str(data['revision'])+'_'+uuid.uuid4().hex+'.json')).write_bytes(self.path.read_bytes())
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        temporary.replace(self.path)
        self.register(self.path)

    def robot_binding(self, cfg=None):
        cfg = cfg or self.manipulation.load()
        return geometry.digest(dict(urdf_sha256=hashlib.sha256(cad_geometry.MODEL.read_bytes()).hexdigest(),
            zero=cfg['model']['servo_zero_deg'], signs=cfg['model']['servo_sign'], arms=arm_fingerprint(self.control.config())))

    def projection_binding(self, legacy_error_limit=None):
        cfg = self.manipulation.load()
        fields=dict(robot=self.robot_binding(cfg), camera=cfg['camera'],
            mount=(cfg.get('cad') or {}).get('camera_mount'), table=cfg['table_z_mm'], block=cfg['block_size_mm'])
        if legacy_error_limit is not None:fields['error_limit']=legacy_error_limit
        return geometry.digest(fields)

    def projection_matches(self,binding):
        # IK acceptance does not change pixel-to-world projection. Preserve
        # certificates from the previous 1-mm default without changing any
        # points, certificates or their independent acceptance thresholds.
        limit=self.manipulation.load()['max_position_error_mm']
        return binding in {self.projection_binding(),self.projection_binding(limit),self.projection_binding(1.),self.projection_binding(1)}

    def status(self):
        with self.control.lock:
            data = self.load()
            native = hasattr(cv2, 'aruco') and hasattr(cv2.aruco, 'CharucoDetector')
            isolated = (self.runtime/'cv2/__init__.py').exists() and (self.runtime.parent/'manifest.json').exists()
            cfg = self.manipulation.load()
            adopted = (cfg.get('cad') or {}).get('camera_calibration', {})
            sessions = copy.deepcopy(list(data['sessions'].values()))
            for session in sessions:
                if session['kind'] == 'position':
                    for point in session['point_checks']:
                        point['record_sha256'] = geometry.digest(point)
                    session['report'] = self.position_report(session, cfg)
                    if not self.projection_matches(session['projection_binding']):
                        session['report']['passed'] = False
                        session['report']['reasons'].append('当前定位参数已经改变，旧检查只供查阅')
            results = list(data['solutions'].values())
            return dict(revision=data['revision'], board=geometry.BOARD, real=self.control.real,
                capability=dict(available=native or isolated, isolated=not native and isolated,
                    message='识别组件就绪' if native or isolated else '缺少独立ArUco识别组件；旧抓放功能仍可使用'),
                sessions=sessions, solutions=results, adopted=adopted,
                setup_sha256=self.manipulation.sha(), has_cad=cfg.get('cad') is not None,
                table_z_mm=cfg.get('table_z_mm'), table_height_source=copy.deepcopy(adopted.get('table_height_source')),
                position_result=self.position_result_status(data),
                physical_grasp_verified=False)

    def solution(self, data, identifier):
        if identifier not in data['solutions']:
            raise ValueError('找不到标定结果，请刷新')
        result = copy.deepcopy(data['solutions'][identifier])
        certificate = result.pop('certificate', None)
        if geometry.digest(result) != certificate:
            raise ValueError('标定结果或证据清单被修改，请重新求解')
        current = hashlib.sha256((ROOT/'charuco_geometry.py').read_bytes()).hexdigest()
        compatible_optics = result['kind'] == 'intrinsics' and result['solver_sha256'] in INTRINSICS_COMPATIBLE_SOLVERS
        if result['solver_sha256'] != current and not compatible_optics:
            raise ValueError('标定算法版本已改变，请保留样本并重新求解')
        if result['board'] != geometry.BOARD:
            raise ValueError('标定板定义与结果不符')
        for ref in result['evidence']:
            capture = self.read_capture(data, ref['id'])
            if capture['jpeg_sha256'] != ref['jpeg_sha256'] or geometry.digest(capture) != ref['metadata_sha256']:
                raise ValueError('标定原始证据与求解记录不符')
        result['certificate'] = certificate
        return result

    def active_optics(self, data):
        cfg = self.manipulation.load()
        identifier = (cfg.get('cad') or {}).get('camera_calibration', {}).get('intrinsics')
        if not identifier:
            raise ValueError('请先完成并采用镜头内参，再开始随臂标定')
        optics = self.solution(data, identifier)
        if not optics['passed'] or not optics['real'] or not self.control.real:
            raise ValueError('随臂实测需要已采用的真实内参')
        return identifier, optics

    def new_session(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            kind = fields.get('kind')
            if kind not in ('intrinsics', 'hand_eye', 'position'):
                raise ValueError('无效标定阶段')
            if kind != 'position':
                rulers = geometry.array(fields.get('rulers_mm'), (2,), '横竖校验尺长度')
                if np.any(abs(rulers-100) > .5):
                    raise ValueError('两条100毫米校验尺均须在99.5～100.5毫米内；请检查打印比例，不自动缩放')
                if fields.get('board_ready') is not True:
                    raise ValueError('请确认纸面平整且与指定版本一致')
            else:
                rulers = np.array([])
                self.verify_applied(self.manipulation.load())
                if not (self.manipulation.load().get('cad') or {}).get('camera_calibration', {}).get('hand_eye'):
                    raise ValueError('独立位置检查需要先采用随臂标定')
            optics_id = None
            if kind == 'hand_eye':
                if fields.get('board_fixed') is not True:
                    raise ValueError('随臂标定必须确认整批采集期间标定板固定不动')
                optics_id, _ = self.active_optics(data)
            if len(data['sessions']) >= 100:
                raise ValueError('已保存100批标定，请先导出归档后整理')
            identifier = uuid.uuid4().hex
            data['sessions'][identifier] = dict(id=identifier, kind=kind, real=self.control.real,
                created_at=datetime.now(timezone.utc).isoformat(), board=geometry.BOARD,
                rulers_mm=rulers.tolist(), board_fixed=kind == 'hand_eye', optics_id=optics_id,
                robot_binding=self.robot_binding(), projection_binding=self.projection_binding() if kind == 'position' else None,
                samples=[], assignments={}, point_checks=[])
            self.store(data, 'new_session')
            return dict(status=self.status(), session_id=identifier)

    def session(self, data, identifier):
        if identifier not in data['sessions']:
            raise ValueError('请先创建或选择一批标定资料')
        session = data['sessions'][identifier]
        if session['real'] != self.control.real:
            raise ValueError('不能混用实机和模拟采集资料')
        if session['kind'] != 'intrinsics' and session['robot_binding'] != self.robot_binding():
            raise ValueError('关节配置或模型已改变，请创建新批次')
        if session['kind'] == 'hand_eye' and self.active_optics(data)[0] != session['optics_id']:
            raise ValueError('采用的内参已改变，请创建新随臂批次')
        if session['kind'] == 'position' and not self.projection_matches(session['projection_binding']):
            raise ValueError('定位参数已改变，请创建新检查批次')
        return session

    def read_capture(self, data, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier) or identifier not in data['captures']:
            raise ValueError('无效标定原图编号')
        meta_path = self.frames/(identifier+'.json')
        image_path = self.frames/(identifier+'.jpg')
        raw = meta_path.read_bytes()
        expected = data['captures'][identifier]
        if hashlib.sha256(raw).hexdigest() != expected['metadata_sha256']:
            raise ValueError('标定画面记录已改变')
        meta = json.loads(raw)
        if meta['id'] != identifier or hashlib.sha256(image_path.read_bytes()).hexdigest() != meta['jpeg_sha256']:
            raise ValueError('标定原图已改变')
        self.register(meta_path)
        self.register(image_path)
        return meta

    def image(self, identifier):
        with self.control.lock:
            self.read_capture(self.load(), identifier)
            return (self.frames/(identifier+'.jpg')).read_bytes()

    def live_image(self):
        # Finite HTTP snapshots let the page finish loading in native browsers.
        # Uses the existing camera daemon; never opens a second camera or a servo.
        camera = self.demo.camera
        with camera.condition:
            if not camera.status()['connected'] or camera.jpeg is None:
                raise ValueError('相机画面未就绪')
            return bytes(camera.jpeg)

    def detect(self, frame, jpeg):
        if hasattr(cv2, 'aruco') and hasattr(cv2.aruco, 'CharucoDetector'):
            return geometry.detect(frame)
        manifest_path = self.runtime.parent/'manifest.json'
        if not manifest_path.exists():
            raise ValueError('独立ArUco识别组件未安装；没有下发任何运动')
        manifest = json.loads(manifest_path.read_text())
        for name, expected in manifest['files'].items():
            path = self.runtime/name
            if path.resolve().is_relative_to(self.runtime.resolve()) is False:
                raise ValueError('无效识别组件路径')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('识别组件文件已改变，请重新核对部署')
        self.register(manifest_path)
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.runtime)
        run = subprocess.run([sys.executable, '-B', str(ROOT/'charuco_worker.py')],
            input=json.dumps(dict(jpeg_base64=base64.b64encode(jpeg).decode())),
            text=True, capture_output=True, env=env, timeout=45, cwd=ROOT)
        try:
            result = json.loads(run.stdout)
        except ValueError:
            raise ValueError('独立识别进程失败：'+run.stderr[-300:])
        if run.returncode or 'error' in result:
            raise ValueError('标定板识别失败：'+result.get('error', run.stderr[-300:]))
        return result['result']

    def capture(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            self.control.audit(dict(operation='calibration_capture_'+session['kind'], moves=0))
            rows = None
            try:
                with self.control.hardware():
                    if session['kind'] != 'intrinsics':
                        rows = self.control.read_rows(self.control.config())
                        checked_snapshot(self.control.config(), rows)
                    frame, jpeg, sequence, captured = AutoTask.frame(self)
                    if rows is not None:
                        after = self.control.read_rows(self.control.config())
                        checked_snapshot(self.control.config(), after)
                        if rows != after:
                            raise ValueError('采样前后读数变化，请停稳后重新采集；未自动重读')
            except Exception as exc:
                self.control.record('calibration_capture_failed', dict(error=str(exc), moves=0))
                raise
            identifier = uuid.uuid4().hex
            detection = None
            if session['kind'] != 'position':
                try:
                    detection = self.detect(frame, jpeg)
                except Exception as exc:
                    detection = dict(acceptable=False, ids=[], pixels=[], marker_ids=[], corner_count=0,
                        marker_count=0, reasons=['识别失败，原图已保留：'+str(exc)], detector_failed=True)
                    self.control.record('calibration_detection_failed', dict(error=str(exc), moves=0))
            elif [frame.shape[1], frame.shape[0]] != self.manipulation.load()['camera']['image_size']:
                raise ValueError('检查原图分辨率与已采用的标定不符')
            meta = dict(id=identifier, session_id=session['id'], real=self.control.real,
                image_size=[frame.shape[1], frame.shape[0]], camera_sequence=sequence, captured_at=captured,
                jpeg_sha256=hashlib.sha256(jpeg).hexdigest(), joints=rows, detection=detection,
                robot_binding=session['robot_binding'])
            if rows is not None:
                pose = cad_geometry.link_matrices(self.manipulation.load(), [r['angle'] for r in rows[:5]])[4].copy()
                pose[:3, 3] *= 1000
                meta['base_to_link4_mm'] = pose.tolist()
            self.frames.mkdir(parents=True, exist_ok=True)
            (self.frames/(identifier+'.jpg')).write_bytes(jpeg)
            raw = json.dumps(meta, ensure_ascii=False, allow_nan=False).encode()
            (self.frames/(identifier+'.json')).write_bytes(raw)
            data['captures'][identifier] = dict(metadata_sha256=hashlib.sha256(raw).hexdigest())
            self.read_capture(data, identifier)
            self.store(data, 'capture')
            return dict(status=self.status(), capture=meta, image_url='/calibration/frame/'+identifier+'.jpg')

    def add(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            row = self.read_capture(data, fields.get('capture_id'))
            if row['session_id'] != session['id'] or row['real'] != session['real']:
                raise ValueError('画面不属于当前批次')
            role = fields.get('role')
            if role not in ('fit', 'check') or session['kind'] == 'position':
                raise ValueError('无效拟合或独立检查分组')
            if not row['detection']['acceptable']:
                raise ValueError('画面质量未通过：'+'；'.join(row['detection']['reasons']))
            previous_role = session.setdefault('assignments', {}).get(row['id'])
            if previous_role is not None and previous_role != role:
                raise ValueError('样本分组已锁定，检查图不能改作拟合图；原始分组记录保留')
            existing = [self.read_capture(data, r['capture_id']) for r in session['samples']]
            if any(r['jpeg_sha256'] == row['jpeg_sha256'] for r in existing):
                raise ValueError('同一原图不能重复加入或同时用作拟合与检查')
            if any(r['image_size'] != row['image_size'] for r in existing):
                raise ValueError('本批原图分辨率已经改变，请重新创建批次')
            for previous in existing:
                if session['kind'] == 'hand_eye':
                    if np.linalg.norm(np.array([r['angle'] for r in row['joints'][:4]])-np.array([r['angle'] for r in previous['joints'][:4]])) < 3:
                        raise ValueError('link4姿态与已有样本过近；只转5号夹爪不能增加手眼信息')
                else:
                    a = dict(zip(previous['detection']['ids'], previous['detection']['pixels']))
                    b = dict(zip(row['detection']['ids'], row['detection']['pixels']))
                    common = sorted(set(a)&set(b))
                    if len(common) >= 12 and np.sqrt(np.mean((np.array([a[i] for i in common])-np.array([b[i] for i in common]))**2)) < 2:
                        raise ValueError('标定板与已有样本几乎相同，请改变位置或倾角再采集')
            if len(session['samples']) >= 60:
                raise ValueError('每批最多60幅，请另建批次')
            session['samples'].append(dict(capture_id=row['id'], role=role, corners=row['detection']['corner_count']))
            session['assignments'][row['id']] = role
            self.store(data, 'add_sample')
            return self.status()

    def remove(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            before = len(session['samples'])
            session['samples'] = [r for r in session['samples'] if r['capture_id'] != fields.get('capture_id')]
            if before == len(session['samples']):
                raise ValueError('未找到这幅样本')
            self.store(data, 'exclude_sample_preserve_original')
            return self.status()

    def solve(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            if session['kind'] == 'position':
                raise ValueError('位置检查直接记录毫米误差，不进行拟合')
            rows = [dict(self.read_capture(data, r['capture_id']), role=r['role']) for r in session['samples']]
            if session['kind'] == 'intrinsics':
                result = geometry.fit_intrinsics(rows)
            else:
                optics = self.solution(data, session['optics_id'])
                result = geometry.fit_hand_eye(rows, optics)
            result.update(id=uuid.uuid4().hex, session_id=session['id'], real=session['real'],
                created_at=datetime.now(timezone.utc).isoformat(), board=geometry.BOARD,
                robot_binding=session['robot_binding'], optics_id=session['optics_id'],
                solver_sha256=hashlib.sha256((ROOT/'charuco_geometry.py').read_bytes()).hexdigest(),
                evidence=[dict(id=r['id'], role=r['role'], jpeg_sha256=r['jpeg_sha256'],
                    metadata_sha256=geometry.digest({k:v for k,v in r.items() if k != 'role'})) for r in rows])
            result['certificate'] = geometry.digest(result)
            data['solutions'][result['id']] = result
            self.store(data, 'solve_'+session['kind'])
            return dict(status=self.status(), result=result)

    @staticmethod
    def recipe(optics):
        return dict(kind='imported', image_size=optics['image_size'], matrix=optics['matrix'],
            distortion=optics['distortion'], source='ChArUco实拍内参 '+optics['id'][:8]+'；图像检查通过，独立空间精度待验收')

    # 用户10-29选择手眼板面作为桌面来源；四角高差上限由3毫米放宽为10毫米并记录。
    BOARD_LEVEL_LIMIT_MM = 10

    def board_plane(self, result, thickness):
        """手眼板面四角平均高度减去板厚；返回高度及四角高差。"""
        if type(thickness) not in (int, float) or not np.isfinite(thickness) or not 0 <= thickness <= 30:
            raise ValueError('请填写实测纸张与底板总厚度，0～30毫米')
        corners = np.array([[0,0,0,1],[180,0,0,1],[0,120,0,1],[180,120,0,1]])@np.array(result['base_to_board_mm']).T
        spread = float(np.ptp(corners[:,2]))
        if spread > self.BOARD_LEVEL_LIMIT_MM:
            raise ValueError('求得板面四角高差'+str(round(spread,1))+'毫米，超过'+str(self.BOARD_LEVEL_LIMIT_MM)+'毫米，不能据此估算板面高度')
        return float(np.mean(corners[:,2])-thickness), spread

    def verify_applied(self, cfg):
        cad = cfg.get('cad')
        ref = (cad or {}).get('camera_calibration')
        if not ref:
            return
        data = self.load()
        optics = self.solution(data, ref.get('intrinsics'))
        if not optics['passed'] or not optics['real']:
            raise ValueError('不能采用模拟或未通过检查的标定结果')
        if cad['intrinsics_recipe'] != self.recipe(optics) or cfg['camera']['image_size'] != optics['image_size']:
            raise ValueError('当前内参来源与标定证据不符')
        table_source = ref.get('table_height_source')
        measured = table_source and table_source.get('kind') == 'measured_model_datum'
        if measured:
            if abs(cfg['table_z_mm']-cad_geometry.measured_table_z(table_source)) > 1e-6:
                raise ValueError('桌面高度与独立实测基准不符')
        if table_source and not measured and not ref.get('hand_eye'):
            raise ValueError('桌面板面估计缺少对应的随臂标定证据')
        projection = ref.get('projection_table')
        if projection is not None:
            if not ref.get('hand_eye') or projection.get('kind') != 'board_pose_estimate':
                raise ValueError('投影板面缺少对应的随臂标定证据')
            z, spread = self.board_plane(self.solution(data, ref['hand_eye']), projection.get('thickness_mm'))
            if abs(projection.get('z_mm', np.nan)-z) > 1e-6 or projection.get('board_corner_spread_mm') != spread:
                raise ValueError('投影板面高度与手眼板面估计不符')
        if ref.get('hand_eye'):
            result = self.solution(data, ref['hand_eye'])
            if not result['passed'] or not result['real'] or result['optics_id'] != optics['id'] or result['robot_binding'] != self.robot_binding(cfg):
                raise ValueError('随臂标定未通过或模型、关节配置、内参已改变')
            if cad['camera_mount'] != result['camera_mount']:
                raise ValueError('相机安装参数与实拍手眼结果不符')
            table_source = ref.get('table_height_source')
            if table_source and not measured:
                if table_source.get('kind') != 'board_pose_estimate':
                    raise ValueError('未知桌面高度来源')
                pose = np.array(result['base_to_board_mm'])
                corners = np.array([[0,0,0,1],[180,0,0,1],[0,120,0,1],[180,120,0,1]])@pose.T
                thickness = table_source.get('thickness_mm')
                if type(thickness) not in (int,float) or not 0 <= thickness <= 30 or np.ptp(corners[:,2]) > self.BOARD_LEVEL_LIMIT_MM:
                    raise ValueError('桌面高度的板面或厚度证据无效')
                if abs(cfg['table_z_mm']-(float(np.mean(corners[:,2]))-thickness)) > 1e-6:
                    raise ValueError('桌面高度与采用的板面估计不符；请重新核对来源')

    def preserve_applied(self, old, candidate):
        ref = (old.get('cad') or {}).get('camera_calibration')
        if ref:
            self.verify_applied(old)
            optics = self.solution(self.load(), ref['intrinsics'])
            if candidate['camera']['image_size'] != optics['image_size']:
                raise ValueError('分辨率已改变，不能沿用已采用的标定')
            recipe = self.recipe(optics)
            k, d, source = cad_geometry.intrinsics(optics['image_size'], recipe)
            candidate['camera'].update(matrix=k, distortion=d)
            candidate['cad'].update(intrinsics_recipe=recipe, intrinsics_source=source, camera_calibration=copy.deepcopy(ref))
            candidate['cad']['image_roll_deg'] = old['cad']['image_roll_deg']
            if ref.get('hand_eye'):
                candidate['cad']['camera_mount'] = self.solution(self.load(), ref['hand_eye'])['camera_mount']
            self.verify_applied(candidate)
        return candidate

    def apply(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            if fields.get('confirmed') is not True:
                raise ValueError('请明确确认采用本次参数；采用本身不运动')
            result = self.solution(data, fields.get('solution_id'))
            if not result['passed'] or not result['real'] or not self.control.real:
                raise ValueError('仅真实采样且检查通过的结果可以采用；模拟结果不能写入实机标定')
            cfg = self.manipulation.load()
            if cfg.get('cad') is None:
                raise ValueError('请先在抓放页生成模型定位参数（不运动），再采用标定结果')
            ref = copy.deepcopy(cfg['cad'].get('camera_calibration', {}))
            if result['kind'] == 'intrinsics':
                if cfg['camera']['image_size'] != result['image_size']:
                    raise ValueError('标定结果分辨率与当前抓放设置不符')
                recipe = self.recipe(result)
                k, d, source = cad_geometry.intrinsics(result['image_size'], recipe)
                cfg['camera'].update(matrix=k, distortion=d)
                cfg['cad'].update(intrinsics_recipe=recipe, intrinsics_source=source)
                if ref.get('intrinsics') != result['id']:
                    mount = copy.deepcopy(cad_geometry.profile()['camera_mount'])
                    mount['rotation_optical_to_link'] = (np.asarray(mount['rotation_optical_to_link'])@cad_geometry.rotation([0,0,1], np.radians(cfg['cad']['image_roll_deg']))).tolist()
                    cfg['cad']['camera_mount'] = mount
                    independent_height = ref.get('table_height_source')
                    ref = dict(intrinsics=result['id'])
                    if (independent_height or {}).get('kind') == 'measured_model_datum':
                        ref['table_height_source'] = independent_height
            else:
                if ref.get('intrinsics') != result['optics_id'] or result['robot_binding'] != self.robot_binding(cfg):
                    raise ValueError('内参或关节模型已改变，请重新采集/求解随臂标定')
                cfg['cad']['camera_mount'] = result['camera_mount']
                ref['hand_eye'] = result['id']
                previous_height = ref.get('table_height_source')
                if (ref.get('table_height_source') or {}).get('kind') != 'measured_model_datum':
                    ref.pop('table_height_source', None)
                ref.pop('projection_table', None)
                if fields.get('board_projection_only') is True:
                    # 用户10-29：板面高度只用于图像换算，运动高度保持实测基准。
                    if fields.get('board_on_table') is True:raise ValueError('板面高度只能二选一：更新桌面或仅用于投影')
                    z, spread = self.board_plane(result, fields.get('board_thickness_mm'))
                    ref['projection_table'] = dict(kind='board_pose_estimate', thickness_mm=fields['board_thickness_mm'],
                        z_mm=z, board_corner_spread_mm=spread, independent_accuracy_verified=False)
                if fields.get('board_on_table') is True:
                    thickness = fields.get('board_thickness_mm')
                    if type(thickness) not in (float, int) or not np.isfinite(thickness) or not 0 <= thickness <= 30:
                        raise ValueError('请填写实测纸张与底板总厚度，0～30毫米')
                    pose = np.array(result['base_to_board_mm'])
                    corners = np.array([[0,0,0,1],[180,0,0,1],[0,120,0,1],[180,120,0,1]])@pose.T
                    level = float(np.ptp(corners[:,2]))
                    if level > self.BOARD_LEVEL_LIMIT_MM:
                        raise ValueError('求得板面四角高差'+str(round(level,1))+'毫米，超过'+str(self.BOARD_LEVEL_LIMIT_MM)+'毫米，不能据此更新桌面高度')
                    cfg['table_z_mm'] = float(np.mean(corners[:,2])-thickness)
                    ref['table_height_source'] = dict(kind='board_pose_estimate', thickness_mm=thickness,
                        board_corner_spread_mm=level, independent_accuracy_verified=False)
                    if (previous_height or {}).get('kind') == 'measured_model_datum':
                        # 原安装板实测仅保留为历史记录，不再参与定位。
                        ref['table_height_source']['superseded_measured_datum'] = previous_height
            cfg['cad']['camera_calibration'] = ref
            self.verify_applied(cfg)
            self.manipulation.save(dict(setup=cfg, expected_sha256=fields.get('setup_sha256')))
            self.store(data, 'apply_'+result['kind'])
            return self.status()

    def set_table_height(self, fields):
        """Store an explicitly confirmed physical height; no arm or camera IO."""
        with self.control.lock:
            data = self.guard(fields)
            if fields.get('confirmed') is not True:
                raise ValueError('请明确确认工作台和基准面的独立实测高度')
            cfg = self.manipulation.load()
            if cfg.get('cad') is None or not cfg['cad'].get('camera_calibration', {}).get('intrinsics'):
                raise ValueError('请先采用模型及镜头内参，再保存桌面实测基准')
            self.verify_applied(cfg)
            p = cad_geometry.profile()
            source = dict(kind='measured_model_datum', reference_surface='base_round_plate_top_v1',
                reference_z_mm=80., measured_height_mm=fields.get('measured_height_mm'),
                work_surface=fields.get('work_surface'), user_confirmed=True,
                urdf_sha256=p['urdf_sha256'], mesh_sha256=p['mesh_sha256']['base_link.STL'],
                independent_accuracy_verified=False, created_at=datetime.now(timezone.utc).isoformat())
            photo_hash = fields.get('photo_sha256')
            if photo_hash is not None:
                if not isinstance(photo_hash, str) or not re.fullmatch('[0-9a-f]{64}', photo_hash):
                    raise ValueError('测量照片指纹无效')
                source['photo_sha256'] = photo_hash
            if not isinstance(source['work_surface'], str) or len(source['work_surface']) > 300:
                raise ValueError('请简要说明实际工作平面')
            cfg['table_z_mm'] = cad_geometry.measured_table_z(source)
            cfg['cad']['camera_calibration']['table_height_source'] = source
            self.verify_applied(cfg)
            self.manipulation.save(dict(setup=cfg, expected_sha256=fields.get('setup_sha256')))
            self.store(data, 'set_measured_table_height')
            return self.status()

    def point_check(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            if session['kind'] != 'position' or fields.get('measured_independently') is not True:
                raise ValueError('请选择独立位置检查，并确认坐标来自实物测量')
            row = self.read_capture(data, fields.get('capture_id'))
            if row['session_id'] != session['id'] or row['joints'] is None:
                raise ValueError('检查图必须属于本批并带有完整六关节读数')
            kind = fields.get('kind')
            if kind not in ('table', 'top'):
                raise ValueError('请选择桌面或方块顶面检查')
            cfg = self.manipulation.load()
            px = geometry.array(fields.get('pixel'), (2,), '原图点击坐标')
            actual = geometry.array(fields.get('actual_xyz_mm'), (3,), '独立测量的底座XYZ')
            height = cfg['table_z_mm']+(cfg['block_size_mm'][2] if kind == 'top' else 0)
            predicted = project_to_plane(cfg, px.tolist(), height, [r['angle'] for r in row['joints'][:5]])
            record = dict(capture_id=row['id'], kind=kind, pixel=px.tolist(), actual_xyz_mm=actual.tolist(),
                predicted_xyz_mm=predicted.tolist(), error_mm=float(np.linalg.norm(predicted-actual)), real=row['real'])
            if any(r['capture_id'] == row['id'] and r['kind'] == kind and np.linalg.norm(np.array(r['actual_xyz_mm'])-actual) < 1 for r in session['point_checks']):
                raise ValueError('同一检查点不能重复计数')
            session['point_checks'].append(record)
            self.store(data, 'independent_point_check')
            return dict(status=self.status(), check=record, report=self.position_report(session, cfg))

    def exclude_point(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            identifier = fields.get('session_id')
            if identifier not in data['sessions'] or data['sessions'][identifier]['kind'] != 'position':
                raise ValueError('请选择独立位置检查批次')
            session = data['sessions'][identifier]
            reason = fields.get('reason')
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                raise ValueError('请记录移出该测量点的原因')
            matches = [i for i, r in enumerate(session['point_checks']) if geometry.digest(r) == fields.get('record_sha256')]
            if len(matches) != 1:
                raise ValueError('检查点已改变或无法唯一定位，请刷新后核对')
            record = session['point_checks'].pop(matches[0])
            session.setdefault('excluded_point_checks', []).append(dict(record=record,
                record_sha256=geometry.digest(record), reason=reason.strip(),
                excluded_at=datetime.now(timezone.utc).isoformat()))
            self.store(data, 'exclude_point_preserve_history')
            return self.status()

    def adopt_position(self, fields):
        with self.control.lock:
            data = self.guard(fields)
            session = self.session(data, fields.get('session_id'))
            if session['kind'] != 'position' or not session['real'] or not self.control.real or fields.get('confirmed') is not True:
                raise ValueError('请明确确认采用本批真实独立位置检查结果')
            limit = fields.get('limit_mm')
            if type(limit) not in (int, float) or not np.isfinite(limit) or limit <= 0:
                raise ValueError('位置检查门槛须为正的有限毫米数值')
            scope = fields.get('scope')
            if scope not in ('table', 'table_and_top'):
                raise ValueError('请明确采用桌面结果或桌面与顶面结果')
            session['position_limit_mm'] = float(limit)
            report = self.position_report(session, self.manipulation.load())
            if not (report['table_passed'] if scope == 'table' else report['passed']):
                raise ValueError('本批所选范围的位置检查尚未通过；点位数量、分布或毫米误差不符合所选门槛')
            rows = [r for r in session['point_checks'] if scope != 'table' or r['kind'] == 'table']
            for row in rows:
                capture = self.read_capture(data, row['capture_id'])
                if not row['real'] or not capture['real'] or capture['session_id'] != session['id']:
                    raise ValueError('采用结果缺少对应的真实原图证据')
            result = dict(id=uuid.uuid4().hex, session_id=session['id'], scope=scope, passed=True,
                limit_mm=float(limit), point_count=len(rows), max_error_mm=max(r['error_mm'] for r in rows),
                mean_error_mm=float(np.mean([r['error_mm'] for r in rows])),
                point_sha256=[geometry.digest(r) for r in rows], projection_binding=session['projection_binding'],
                created_at=datetime.now(timezone.utc).isoformat(), grasp_verified=False,
                excluded_count=len(session.get('excluded_point_checks', [])))
            result['certificate'] = geometry.digest(result)
            data.setdefault('position_results', {})[result['id']] = result
            data['adopted_position_result'] = result['id']
            self.store(data, 'adopt_scoped_position_result')
            return self.status()

    def position_result_status(self, data):
        result = copy.deepcopy(data.get('position_results', {}).get(data.get('adopted_position_result')))
        if not result:
            return None
        certificate = result.pop('certificate', None)
        valid = certificate == geometry.digest(result)
        result['certificate'] = certificate
        session = data['sessions'].get(result['session_id'])
        if not session or not self.projection_matches(result['projection_binding']):
            valid = False
        else:
            rows = [r for r in session['point_checks'] if result['scope'] != 'table' or r['kind'] == 'table']
            valid = valid and result['point_sha256'] == [geometry.digest(r) for r in rows] and session.get('position_limit_mm') == result['limit_mm']
        result['valid'] = bool(valid)
        return result

    @staticmethod
    def position_report(session, cfg):
        reasons = []
        limit = session.get('position_limit_mm', cfg['max_position_error_mm'])
        passed_by_kind = {}
        for kind in ('table', 'top'):
            rows = [r for r in session['point_checks'] if r['kind'] == kind]
            xy = np.array([r['actual_xyz_mm'][:2] for r in rows])
            enough = len(rows) >= 3 and len({tuple(p) for p in xy}) >= 3 and np.linalg.matrix_rank(xy-xy.mean(axis=0)) >= 2
            if not enough:
                reasons.append(('桌面' if kind == 'table' else '顶面')+'需至少3个分散、不共线的独立位置')
            passed_by_kind[kind] = enough and session['real'] and all(r['error_mm'] <= limit for r in rows)
        errors = [r['error_mm'] for r in session['point_checks']]
        if any(e > limit for e in errors):
            reasons.append('存在超出当前允许毫米误差的点；不删除失败记录')
        if not session['real']:
            reasons.append('模拟资料不能作为实机验收')
        return dict(passed=not reasons, reasons=reasons, errors_mm=errors,
            limit_mm=limit, table_passed=passed_by_kind['table'], top_passed=passed_by_kind['top'],
            grasp_verified=False)

    def export(self):
        with self.control.lock:
            return dict(board=geometry.BOARD, data=self.load(),
                note='原图存放于logs/calibration/frames；JSON中的frame编号可从/calibration/frame/<id>.jpg下载；标定不代表抓取成功')
