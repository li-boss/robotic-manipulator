"""语音控制模式（用户11-19）：会话门控、姿态/舵机/跳舞/抓方块四类语音动作。全合成相机与假舵机，绝不连接硬件。"""
import base64
from pathlib import Path
import sys
import tempfile
import time
import unittest

import simulate
from test_modes import expected_calls

VERTICAL_ID, OBSERVE_ID = '0' * 32, '1' * 32


class VoiceModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        simulate.SimFake.max_delay_ms = 0
        self.demo = simulate.application(Path(self.tmp.name))
        self.modes, self.control = self.demo.modes, self.demo.control
        simulate.SimFake.calls.clear()
        self.n = 0

    def tearDown(self):
        self.wait()
        self.tmp.cleanup()

    def wait(self, timeout=60):
        deadline = time.monotonic() + timeout
        while self.control.busy and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.control.busy)

    def rid(self):
        self.n += 1
        return f'{self.n:032x}'

    def session(self, **extra):
        fields = dict(kind='voice_control', vertical_state_id=VERTICAL_ID, observe_state_id=OBSERVE_ID,
                      servo_motion_ms=800, destination='模拟放置点', max_actions=10, cooldown_s=0)
        fields.update(extra)
        offer = self.modes.preview(fields)
        return self.modes.arm(dict(token=offer['token'], confirmed=True))['session']

    def ask(self, call, session, **fields):
        return call(dict(session_id=session['id'], request_id=self.rid(), text=fields.pop('text', '测试语音'), **fields))

    # ---------------------------------------------------------------- 门控与预览
    def test_voice_requests_rejected_until_voice_session_is_armed(self):
        calls = (lambda sid: self.modes.api_voice_pose(dict(session_id=sid, request_id='a' * 32, pose='vertical', text='回到竖直')),
                 lambda sid: self.modes.api_voice_servo(dict(session_id=sid, request_id='a' * 32, joint=1, angle=90, text='一号舵机转到90度')),
                 lambda sid: self.modes.api_voice_dance(dict(session_id=sid, request_id='a' * 32, routine='MUSIC_DANCE_ROUTINE', text='跳舞')),
                 lambda sid: self.modes.api_voice_grasp(dict(session_id=sid, text='抓方块')))
        for call in calls:
            with self.assertRaisesRegex(ValueError, '模式未开启'):
                call(None)
        offer = self.modes.preview(dict(kind='gesture_dance', base_state_id=VERTICAL_ID))
        other = self.modes.arm(dict(token=offer['token'], confirmed=True))['session']
        for call in calls:
            with self.assertRaisesRegex(ValueError, '不是语音控制'):
                call(other['id'])
        self.modes.disarm()
        session = self.session()
        for call in calls:
            with self.assertRaisesRegex(ValueError, '会话编号不符'):
                call('f' * 32)
        self.assertEqual(simulate.SimFake.calls, [])

    def test_preview_lists_every_voice_action_and_requires_explicit_choices(self):
        with self.assertRaisesRegex(ValueError, '竖直姿态'):
            self.modes.preview(dict(kind='voice_control', observe_state_id=OBSERVE_ID, servo_motion_ms=800, grasp_enabled=False))
        with self.assertRaisesRegex(ValueError, 'motion_ms'):
            self.modes.preview(dict(kind='voice_control', vertical_state_id=VERTICAL_ID, observe_state_id=OBSERVE_ID,
                                    servo_motion_ms=300, grasp_enabled=False))
        with self.assertRaisesRegex(ValueError, '放置点'):
            self.modes.preview(dict(kind='voice_control', vertical_state_id=VERTICAL_ID, observe_state_id=OBSERVE_ID,
                                    servo_motion_ms=800))
        plan = self.modes.preview(dict(kind='voice_control', vertical_state_id=VERTICAL_ID, observe_state_id=OBSERVE_ID,
                                       servo_motion_ms=800, destination='模拟放置点'))['plan']
        self.assertEqual(plan['poses']['vertical']['angles'], [90] * 6)
        self.assertEqual(plan['base_state']['id'], VERTICAL_ID)            # 跳舞从竖直姿态出发
        self.assertEqual(sorted(plan['routines']), ['COURSE_DANCE_ROUTINE', 'MUSIC_DANCE_ROUTINE'])
        self.assertEqual(plan['servo']['motion_ms'], 800)
        limits = self.control.config()['joint_limits']
        self.assertEqual(plan['servo']['limits'], {k: v for k, v in limits.items() if v})
        self.assertEqual(plan['grasp']['destination'], '模拟放置点')
        no_grasp = self.modes.preview(dict(kind='voice_control', vertical_state_id=VERTICAL_ID, observe_state_id=OBSERVE_ID,
                                           servo_motion_ms=800, grasp_enabled=False))['plan']
        self.assertIsNone(no_grasp['grasp'])
        self.assertEqual(simulate.SimFake.calls, [])

    # ---------------------------------------------------------------- 姿态与舵机
    def test_pose_runs_saved_button_in_its_order(self):
        session = self.session()
        self.ask(self.modes.api_voice_pose, session, pose='observe', text='回到观察姿态')
        self.wait()
        state = session['plan']['poses']['observe']
        self.assertEqual(simulate.SimFake.calls, [[(j, state['angles'][j - 1])] for j in state['order']])
        self.assertEqual(self.control.job['status'], 'completed')
        self.assertEqual(self.modes.api_mode()['used'], 1)
        with self.assertRaises(ValueError):
            self.ask(self.modes.api_voice_pose, session, pose='home')

    def test_servo_moves_one_joint_with_session_timing_and_never_clips(self):
        session = self.session()
        result = self.ask(self.modes.api_voice_servo, session, joint=3, angle=100, text='三号舵机转到100度')
        self.wait()
        self.assertEqual(simulate.SimFake.calls, [[(3, 100)]])
        self.assertEqual(self.control.job['parameters']['motion_ms'], 800)
        self.assertEqual(self.control.job['parameters']['trigger']['text'], '三号舵机转到100度')
        self.assertEqual(result['used'], 1)
        low, high = session['plan']['servo']['limits']['3']
        for joint, angle in ((3, high + 1), (3, low - 1), (3, 100.5), (7, 90), ('3', 90), (3, True)):
            with self.assertRaises(ValueError):
                self.ask(self.modes.api_voice_servo, session, joint=joint, angle=angle)
        self.assertEqual(simulate.SimFake.calls, [[(3, 100)]])
        self.assertEqual(self.modes.api_mode()['used'], 1)

    def test_request_id_dedupes_and_text_is_required(self):
        session = self.session()
        fields = dict(session_id=session['id'], request_id='d' * 32, joint=1, angle=80, text='一号转到80度')
        self.modes.api_voice_servo(fields)
        self.wait()
        self.assertTrue(self.modes.api_voice_servo(fields)['duplicate'])
        self.wait()
        self.assertEqual(simulate.SimFake.calls, [[(1, 80)]])
        with self.assertRaisesRegex(ValueError, 'text'):
            self.modes.api_voice_servo(dict(fields, request_id='e' * 32, text=''))

    def test_disarm_stops_remaining_pose_joints(self):
        session = self.session()
        simulate.SimFake.max_delay_ms = 80
        self.ask(self.modes.api_voice_pose, session, pose='observe')
        time.sleep(.12)
        self.modes.disarm()
        self.wait()
        self.assertLess(len(simulate.SimFake.calls), 6)
        self.assertEqual(self.control.job['status'], 'stopped')

    def test_session_limit_counts_every_voice_action(self):
        session = self.session(max_actions=2)
        self.ask(self.modes.api_voice_servo, session, joint=1, angle=80)
        self.wait()
        self.ask(self.modes.api_voice_servo, session, joint=1, angle=90)
        self.wait()
        self.assertIsNone(self.modes.api_mode()['session_id'])      # 次数用完，会话结束
        with self.assertRaises(ValueError):
            self.ask(self.modes.api_voice_servo, session, joint=1, angle=100)

    # ---------------------------------------------------------------- 跳舞
    def test_voice_and_gesture_dance_use_previewed_routine(self):
        session = self.session(cooldown_s=30)
        result = self.ask(self.modes.api_voice_dance, session, routine='COURSE_DANCE_ROUTINE', text='跳课程舞蹈')
        self.wait()
        self.assertEqual(self.control.job['status'], 'completed')
        steps = session['plan']['routines']['COURSE_DANCE_ROUTINE']['steps']
        self.assertEqual(simulate.SimFake.calls, expected_calls(steps, [90, 80, 35, 10, 90, 90]))  # 先同批回到竖直
        self.assertEqual(result['used'], 1)
        with self.assertRaisesRegex(ValueError, '不足30秒'):
            self.ask(self.modes.api_voice_dance, session, routine='MUSIC_DANCE_ROUTINE', trigger='gesture',
                     label='palm', confidence=.9)
        # 冷却只针对跳舞；舵机命令不受影响
        self.ask(self.modes.api_voice_servo, session, joint=1, angle=80)
        self.wait()
        with self.assertRaisesRegex(ValueError, 'label'):
            self.ask(self.modes.api_voice_dance, session, routine='MUSIC_DANCE_ROUTINE', trigger='gesture')
        with self.assertRaisesRegex(ValueError, '未授权'):
            self.ask(self.modes.api_voice_dance, session, routine='OTHER')

    # ---------------------------------------------------------------- 抓方块
    def _grasp_once(self, session, text='抓方块'):
        request = self.modes.api_voice_grasp(dict(session_id=session['id'], text=text))['request_id']
        self.assertEqual(self.modes.api_mode()['grasp_request'], request)
        frame = self.modes.api_block_frame(dict(session_id=session['id'], request_id=request))
        self.assertEqual(simulate.SimFake.calls, [])            # 冻结不运动
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pc'))
        import cv2
        import numpy as np
        from block_vision.color_detector import ColorBlockDetector
        image = cv2.imdecode(np.frombuffer(base64.b64decode(frame['jpeg_base64']), np.uint8), cv2.IMREAD_COLOR)
        detection = ColorBlockDetector().detect(image)[0].to_dict()
        return self.modes.api_block_target(dict(session_id=session['id'], request_id=request,
                                                frame_id=frame['frame_id'], detection=detection))

    def test_voice_grasp_runs_one_full_detection_cycle(self):
        session = self.session()
        result = self._grasp_once(session, '帮我抓一下方块')
        self.wait()
        self.assertEqual(self.control.job['status'], 'completed')
        self.assertEqual(len(simulate.SimFake.calls), result['steps'])
        self.assertEqual(self.modes.status()['session']['last_result']['status'], 'started')
        self.assertEqual(result['target']['task_mode'], 'pick_place')
        with self.assertRaisesRegex(ValueError, '开始识别夹取|编号'):   # 一次语音只对应一次冻结
            self.modes.api_block_frame(dict(session_id=session['id'], request_id='a' * 32))

    def test_voice_grasp_needs_grasp_enabled_and_pickup_only_once(self):
        session = self.session(grasp_enabled=False)
        with self.assertRaisesRegex(ValueError, '没有开启抓方块'):
            self.modes.api_voice_grasp(dict(session_id=session['id'], text='抓方块'))
        self.modes.disarm()
        session = self.session(task_mode='pickup')
        self._grasp_once(session)
        self.wait()
        with self.assertRaisesRegex(ValueError, '只能抓1次'):
            self.modes.api_voice_grasp(dict(session_id=session['id'], text='再抓一个'))
        # 只夹取后其他语音动作仍可用（次数未用完）
        self.ask(self.modes.api_voice_servo, session, joint=1, angle=80)
        self.wait()
        self.assertEqual(self.control.job['status'], 'completed')

    def test_web_start_button_is_not_used_for_voice_sessions(self):
        session = self.session()
        with self.assertRaisesRegex(ValueError, '不是方块识别自动抓取'):
            self.modes.start_grasp(dict(session_id=session['id']))


if __name__ == '__main__':
    unittest.main()
