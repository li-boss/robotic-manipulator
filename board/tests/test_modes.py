"""识别模式（自动抓取 / 跳舞）的门控与执行：全合成相机与假舵机，绝不连接硬件。"""
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import simulate
from dance import build_routine, offsets
from modes import detection_to_target

BASE_ID = '0' * 32
OTHER_ID = '1' * 32     # 模拟器里非竖直的姿态按钮（课程舞蹈不能从这里出发）


class ModesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        simulate.SimFake.max_delay_ms = 0
        self.demo = simulate.application(Path(self.tmp.name))
        self.modes, self.control = self.demo.modes, self.demo.control
        simulate.SimFake.calls.clear()

    def tearDown(self):
        self.wait()
        self.tmp.cleanup()

    def wait(self, timeout=60):
        deadline = time.monotonic() + timeout
        while self.control.busy and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.control.busy)

    def arm(self, **fields):
        offer = self.modes.preview(fields)
        return self.modes.arm(dict(token=offer['token'], confirmed=True))['session']

    def dance_session(self, **extra):
        return self.arm(kind='gesture_dance', base_state_id=BASE_ID, max_actions=2, cooldown_s=0, **extra)

    def block_session(self, **extra):
        fields = dict(kind='block_grasp', task_mode='pick_place', destination='模拟放置点', max_actions=2)
        fields.update(extra)
        return self.arm(**fields)

    # ---------------------------------------------------------------- 门控
    def test_pc_requests_rejected_until_armed_and_bound_to_session(self):
        for call in (lambda: self.modes.api_block_frame({}),
                     lambda: self.modes.api_gesture(dict(routine='music', request_id='a' * 32))):
            with self.assertRaises(ValueError):
                call()
        session = self.dance_session()
        with self.assertRaises(ValueError):   # 会话编号不符
            self.modes.api_gesture(dict(session_id='f' * 32, routine='music', request_id='a' * 32))
        with self.assertRaises(ValueError):   # 模式类型不符
            self.modes.api_block_frame(dict(session_id=session['id']))
        self.assertEqual(simulate.SimFake.calls, [])

    def test_arm_requires_preview_token_confirmation_and_unchanged_plan(self):
        offer = self.modes.preview(dict(kind='gesture_dance', base_state_id=BASE_ID))
        with self.assertRaises(ValueError):
            self.modes.arm(dict(token=offer['token']))          # 未勾选确认
        offer = self.modes.preview(dict(kind='gesture_dance', base_state_id=BASE_ID))
        dances = self.demo.data_dir / 'dances.json'
        data = json.loads(dances.read_text())
        data['routines']['music']['keyframes'][0]['1'] = 20
        dances.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.modes.arm(dict(token=offer['token'], confirmed=True))   # 预览后动作改变
        self.assertIsNone(self.modes.session)

    def test_unready_hardware_cannot_arm(self):
        self.control.real = True
        arms = json.loads(self.control.path.read_text());arms['hardware_ready'] = False
        self.control.path.write_text(json.dumps(arms))
        with self.assertRaises(ValueError):
            self.modes.preview(dict(kind='gesture_dance', base_state_id=BASE_ID))

    # ---------------------------------------------------------------- 跳舞
    def test_dance_executes_exact_previewed_steps_and_dedupes(self):
        session = self.dance_session()
        planned = session['plan']['routines']['music']['steps']
        request = dict(session_id=session['id'], routine='music', request_id='b' * 32, label='smile', confidence=.9)
        self.modes.api_gesture(request);self.wait()
        self.assertEqual(simulate.SimFake.calls, [[tuple(p) for p in s['joints']] for s in planned])
        again = self.modes.api_gesture(request)
        self.assertTrue(again['duplicate']);self.assertEqual(len(simulate.SimFake.calls), len(planned))
        self.assertEqual(self.control.job['status'], 'completed')

    def test_dance_limits_cooldown_and_unauthorized_routine(self):
        session = self.arm(kind='gesture_dance', base_state_id=BASE_ID, routines=['music'], max_actions=1, cooldown_s=30)
        with self.assertRaises(ValueError):
            self.modes.api_gesture(dict(session_id=session['id'], routine='course', request_id=secrets.token_hex(16)))
        self.modes.api_gesture(dict(session_id=session['id'], routine='music', request_id=secrets.token_hex(16)))
        self.wait()
        with self.assertRaises(ValueError):    # 次数用完，会话结束
            self.modes.api_gesture(dict(session_id=session['id'], routine='music', request_id=secrets.token_hex(16)))
        self.assertIsNone(self.modes.session)

    def test_out_of_range_dance_rejected_without_clipping(self):
        arms = json.loads(self.control.path.read_text())
        state = dict(id=BASE_ID, name='边缘', angles=[90, 100, 60, 15, 90, 90], order=[1, 2, 3, 4, 5, 6], motion_ms=1000)
        with self.assertRaises(ValueError):    # 4号 15-15=0 < 10
            build_routine(arms, state, 'nod', dict(pattern='nod', amplitude_deg=15, repeats=1, motion_ms=700))
        for bad in (dict(pattern='nod', amplitude_deg=31, repeats=1, motion_ms=700),
                    dict(pattern='nod', amplitude_deg=5, repeats=1, motion_ms=499),
                    dict(pattern='spin', amplitude_deg=5, repeats=1, motion_ms=700),
                    dict(pattern='nod', amplitude_deg=5.5, repeats=1, motion_ms=700)):
            with self.assertRaises(ValueError):
                build_routine(arms, dict(state, angles=[90, 100, 60, 60, 90, 90]), 'x', bad)

    # ---------------------------------------------------------------- 网页跳舞按钮
    def web_preview(self, routine='course', state=BASE_ID):
        return self.modes.dance_preview(dict(routine=routine, state_id=state))

    def web_start(self, preview, **extra):
        fields = dict(routine=preview['routine']['name'], state_id=preview['routine']['base_state']['id'],
                      routine_sha256=preview['routine']['sha256'], states_sha256=preview['states_sha256'],
                      request_id=secrets.token_hex(16), confirmed=True)
        fields.update(extra)
        return self.modes.dance_start(fields)

    def test_course_dance_matches_course_material_with_marked_adjustments(self):
        from dance import COURSE_KEYFRAMES
        preview = self.web_preview()
        beats = [s for s in preview['routine']['steps'] if s['label'].startswith('课程舞蹈')]
        self.assertEqual(len(beats), 17)
        # 课程原文第1拍：2号180-120=60、3号120、4号60；第15拍夹爪用time_2=1000 ms
        self.assertEqual(beats[0]['joints'], [[2, 60], [3, 120], [4, 60]])
        self.assertEqual(beats[14]['motion_ms'], 1000)
        # 越界值只在模板里人工明确改写，并随计划一起展示
        self.assertEqual(beats[11]['joints'], [[1, 10], [4, 90], [5, 90], [6, 90]])
        self.assertEqual(beats[12]['joints'], [[3, 170], [4, 10]])
        self.assertEqual(beats[13]['joints'], [[6, 170]])
        self.assertIn('180→170', preview['routine']['adjusted'])
        self.assertEqual(len(COURSE_KEYFRAMES), 17)
        self.assertEqual([v for _, v in beats[-1]['joints']], [90, 90])

    def test_music_dance_matches_tutorial_with_marked_adjustments(self):
        from dance import MUSIC_KEYFRAMES
        preview = self.web_preview('music')
        beats = [s for s in preview['routine']['steps'] if s['label'].startswith('音乐舞蹈')]
        self.assertEqual(len(beats), 26)
        # 教程起手 write6(0,90,0,180,90,90,2000) → 本机 1号10、3号10、4号170
        self.assertEqual(beats[0]['joints'], [[1, 10], [2, 90], [3, 10], [4, 170], [5, 90], [6, 90]])
        self.assertEqual(beats[0]['motion_ms'], 2000)
        self.assertEqual([b['motion_ms'] for b in beats[1:8]], [1600] * 7)
        self.assertEqual([b['motion_ms'] for b in beats[8:24]], [600] * 16)
        # 教程 (180,90,0,180,180,180) → 5号180→170 等，越界值只在模板里人工写明
        self.assertEqual(beats[12]['joints'], [[1, 170], [2, 90], [3, 10], [4, 170], [5, 170], [6, 170]])
        self.assertEqual(beats[10]['joints'][4], [5, 90])           # 教程5号0°，本机下限90°
        self.assertEqual(beats[-1]['joints'], [[1, 90], [2, 90], [3, 10], [4, 90], [5, 90], [6, 170]])
        self.assertIn('0→90', preview['routine']['adjusted'])
        self.assertEqual(len(MUSIC_KEYFRAMES), 26)
        self.assertTrue(preview['collision']['ok'])
        self.assertEqual(simulate.SimFake.calls, [])

    def test_keyframes_require_matching_start_pose_and_never_clip(self):
        with self.assertRaisesRegex(ValueError, '需要起始姿态'):
            self.web_preview('course', OTHER_ID)
        dances = self.demo.data_dir / 'dances.json'
        data = json.loads(dances.read_text());data['routines']['course']['keyframes'][12]['3'] = 180;dances.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '180'):
            self.web_preview('course')
        data['routines']['course']['keyframes'][12]['3'] = 170.0;dances.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '整数'):
            self.web_preview('course')
        self.assertEqual(simulate.SimFake.calls, [])

    def test_web_button_runs_exact_previewed_course_and_dedupes(self):
        preview = self.web_preview()
        self.assertTrue(preview['collision']['checked'] and preview['collision']['ok'])
        self.assertEqual(simulate.SimFake.calls, [])            # 预览不运动
        fields = dict(request_id='c' * 32)
        self.web_start(preview, **fields);self.wait()
        self.assertEqual(simulate.SimFake.calls, [[tuple(p) for p in s['joints']] for s in preview['routine']['steps']])
        again = self.web_start(preview, **fields)
        self.assertTrue(again['duplicate']);self.assertEqual(len(simulate.SimFake.calls), len(preview['routine']['steps']))

    def test_web_button_requires_confirmation_and_current_plan(self):
        preview = self.web_preview('music', BASE_ID)
        with self.assertRaises(ValueError):
            self.web_start(preview, confirmed=False)
        with self.assertRaisesRegex(ValueError, '不同'):
            self.web_start(preview, routine_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, '变化'):
            self.web_start(preview, states_sha256='0' * 64)
        dances = self.demo.data_dir / 'dances.json'
        data = json.loads(dances.read_text());data['routines']['music']['keyframes'][0]['1'] = 20;dances.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '不同'):
            self.web_start(preview)
        self.assertEqual(simulate.SimFake.calls, [])

    def test_web_button_rejects_model_collision_and_out_of_range(self):
        path = self.demo.data_dir / 'manual_states.json'
        data = json.loads(path.read_text());data['states'][0]['angles'] = [90, 45, 45, 45, 90, 90];path.write_text(json.dumps(data))
        dances = self.demo.data_dir / 'dances.json'
        spec = json.loads(dances.read_text())
        # 偏移类模板仍受支持；默认预设已无画圈，这里临时加一套大幅度画圈来触发模型碰撞
        spec['routines']['circle'] = dict(label='画圈', pattern='circle', amplitude_deg=30, repeats=1, motion_ms=600)
        dances.write_text(json.dumps(spec))
        preview = self.web_preview('circle', BASE_ID)
        self.assertFalse(preview['collision']['ok'])
        with self.assertRaisesRegex(ValueError, '碰撞'):
            self.web_start(preview)
        data['states'][0]['angles'] = [10, 100, 60, 60, 130, 90];path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, '不在'):
            self.web_preview('circle', BASE_ID)
        self.assertEqual(simulate.SimFake.calls, [])

    def test_web_button_blocked_while_mode_armed_and_stop_halts_later_beats(self):
        self.dance_session()
        with self.assertRaisesRegex(ValueError, '关闭模式'):
            self.web_start(self.web_preview('music', BASE_ID))
        self.modes.disarm()
        simulate.SimFake.max_delay_ms = 200
        preview = self.web_preview()
        self.web_start(preview)
        deadline = time.monotonic() + 10
        while len(simulate.SimFake.calls) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.modes.dance_stop({})['stopped'])
        self.wait()
        self.assertEqual(self.control.job['status'], 'stopped')
        self.assertLess(len(simulate.SimFake.calls), len(preview['routine']['steps']))

    def test_circle_returns_to_base_and_stays_within_amplitude(self):
        steps = offsets('circle', 10, 1)
        self.assertEqual(steps[-1], {1: 0, 4: 0})
        self.assertTrue(all(abs(v) <= 10 for s in steps for v in s.values()))

    def test_disarm_during_dance_stops_remaining_steps(self):
        session = self.dance_session()
        simulate.SimFake.max_delay_ms = 50
        self.modes.api_gesture(dict(session_id=session['id'], routine='music', request_id='c' * 32))
        time.sleep(.2)
        self.modes.disarm()
        self.wait()
        total = len(session['plan']['routines']['music']['steps'])
        self.assertLess(len(simulate.SimFake.calls), total)
        self.assertEqual(self.control.job['status'], 'stopped')

    def test_write_failure_ends_session_without_retry_of_later_steps(self):
        session = self.dance_session()
        original = simulate.SimFake.write

        def failing(fake, pairs, hold_ms=None, retries=0):
            if len(simulate.SimFake.calls) == 3:
                raise RuntimeError('synthetic I2C error')
            return original(fake, pairs, hold_ms, retries)
        with patch.object(simulate.SimFake, 'write', failing):
            self.modes.api_gesture(dict(session_id=session['id'], routine='music', request_id='d' * 32))
            self.wait()
        self.assertEqual(len(simulate.SimFake.calls), 3)
        self.assertEqual(self.control.job['status'], 'failed')
        self.modes.api_mode()
        self.assertIsNone(self.modes.session)

    def test_code_or_config_change_ends_session(self):
        session = self.dance_session()
        arms = json.loads(self.control.path.read_text());arms['motion_ms'] = 1600
        self.control.path.write_text(json.dumps(arms))
        with self.assertRaises(ValueError):
            self.modes.api_gesture(dict(session_id=session['id'], routine='music', request_id='e' * 32))
        self.assertIsNone(self.modes.session);self.assertEqual(simulate.SimFake.calls, [])

    # ---------------------------------------------------------------- 方块抓取
    def test_block_grasp_full_flow_from_vision_detection(self):
        session = self.block_session()
        frame = self.modes.api_block_frame(dict(session_id=session['id']))
        self.assertEqual(simulate.SimFake.calls, [])            # 冻结不运动
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pc'))
        import base64
        import cv2
        import numpy as np
        from block_vision.color_detector import ColorBlockDetector
        image = cv2.imdecode(np.frombuffer(base64.b64decode(frame['jpeg_base64']), np.uint8), cv2.IMREAD_COLOR)
        detection = ColorBlockDetector().detect(image)[0].to_dict()
        result = self.modes.api_block_target(dict(session_id=session['id'], frame_id=frame['frame_id'], detection=detection))
        self.wait()
        self.assertEqual(self.control.job['status'], 'completed')
        self.assertEqual(len(simulate.SimFake.calls), result['steps'])
        self.assertLess(np.linalg.norm(np.asarray(result['target']['xyz_mm'][:2]) - [0, 260]), 8)
        events, _ = self.demo.store.latest()
        self.assertEqual(events[-1]['source'], 'vision')

    def test_low_score_and_unknown_frame_never_plan(self):
        session = self.block_session(min_score=.8)
        frame = self.modes.api_block_frame(dict(session_id=session['id']))
        with self.assertRaises(ValueError):
            self.modes.api_block_target(dict(session_id=session['id'], frame_id=frame['frame_id'],
                                             detection=dict(bbox=[300, 300, 340, 340], score=.5)))
        with self.assertRaises((ValueError, OSError)):
            self.modes.api_block_target(dict(session_id=session['id'], frame_id='1' * 32,
                                             detection=dict(bbox=[300, 300, 340, 340], score=.9)))
        self.assertEqual(simulate.SimFake.calls, []);self.assertIsNone(self.control.pending)

    def test_pickup_mode_forces_single_action(self):
        session = self.block_session(task_mode='pickup', max_actions=5)
        self.assertEqual(session['max_actions'], 1)

    def test_detection_conversion(self):
        corners = [[100, 100], [140, 100], [140, 140], [100, 140]]
        t = detection_to_target(dict(corners=corners), [640, 480], 'a' * 32, 'pick_place', 'p', 'block')
        self.assertEqual(t['center_px'], [120, 120]);self.assertEqual(t['edge_px'], [[100, 100], [140, 100]])
        self.assertEqual(t['source'], 'vision');self.assertEqual(t['destination'], dict(kind='fixed', name='p'))
        t = detection_to_target(dict(bbox=[100, 100, 140, 140], angle_deg=90), [640, 480], 'a' * 32, 'pickup', None, 'block')
        self.assertNotIn('destination', t);self.assertAlmostEqual(t['edge_px'][0][0], 120)
        for bad in (dict(), dict(corners=[[1, 2]] * 3), dict(bbox=[1, 2, 3]), dict(bbox=[0, 0, 2, 2]),
                    dict(corners=[[0, 0], [1, 1], [2, 2], [3, 3]]), dict(bbox=[10, 10, 50, 50], angle_deg='x')):
            with self.assertRaises(ValueError):
                detection_to_target(bad, [640, 480], 'a' * 32, 'pickup', None, 'block')


class NinePointSourceTests(unittest.TestCase):
    def test_missing_data_uses_pure_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            demo = simulate.application(Path(folder))
            self.assertIsNone(demo.manipulation.planning_reference(demo.manipulation.load()))
            self.assertFalse(demo.manipulation.reference_status(demo.manipulation.load())['available'])

    def test_bound_rejects_changed_arm_config_and_tampered_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            demo = simulate.application(Path(folder))
            source = demo.nine_point
            setup = dict(version=1, arm_config_sha256='0' * 64, baseline=None, common={}, samples=[], validations=[])
            with self.assertRaises(ValueError):
                source.bound(setup)
            from nine_point_source import digest
            setup['arm_config_sha256'] = digest(demo.control.config())
            setup['samples'] = [dict(id='ab' * 16, evidence=dict(file='ab' * 16 + '.json', sha256='0' * 64))]
            source.teaching_logs.mkdir(parents=True)
            (source.teaching_logs / ('ab' * 16 + '.json')).write_text('{}')
            setup['baseline'] = None
            with self.assertRaises(ValueError):
                source.bound(setup)


if __name__ == '__main__':
    unittest.main()
