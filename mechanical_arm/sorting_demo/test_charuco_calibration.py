"""Calibration math, provenance and no-motion tests. Synthetic images/fake hardware only."""
from contextlib import nullcontext
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import charuco_geometry as g
from charuco_task import Calibration
from control import Control
from manipulation_task import Manipulation
from test_manipulation import fixture, StatefulFake
from test_cad_manipulation import case
from core import EventStore


K = np.array([[700.,0,320],[0,710.,240],[0,0,1.]])
D = np.array([-.04,.01,.0002,-.0003,0.])


def projected_row(i, rotation, translation, role='fit'):
    pixels = cv2.projectPoints(g.POINTS, np.array(rotation,float), np.array(translation,float), K, D)[0].reshape(-1,2)
    return dict(id=('%032x'%i), jpeg_sha256=('%064x'%i), image_size=[640,480], role=role,
        detection=dict(ids=list(range(40)), pixels=pixels.tolist(), acceptable=True))


def intrinsic_rows():
    rows=[]
    for i in range(16):
        rows.append(projected_row(i+1,[.28*np.sin(i*1.3),.3*np.cos(i*.9),.12*np.sin(i)],
            [-90+45*np.sin(i*1.7),-60+35*np.cos(i*1.2),380+35*np.sin(i*.7)], 'fit' if i<12 else 'check'))
    return rows


def hand_eye_rows():
    mount=g.transform(cv2.Rodrigues(np.array([.06,-.04,.03]))[0],[10,-15,60])
    board=g.transform(np.eye(3),[-90,-60,500])
    rows=[]
    for i in range(14):
        robot=g.transform(cv2.Rodrigues(np.array([.10*np.sin(i*1.4),.12*np.cos(i*.8),.08*np.sin(i*.6)]))[0],
                          [15*np.sin(i),12*np.cos(i*1.2),5*np.sin(i*.8)])
        camera_board=np.linalg.inv(mount)@np.linalg.inv(robot)@board
        row=projected_row(i+1,cv2.Rodrigues(camera_board[:3,:3])[0].reshape(3),camera_board[:3,3], 'fit' if i<10 else 'check')
        row['base_to_link4_mm']=robot.tolist();rows.append(row)
    return rows,mount,board


def board_image():
    board=g.make_board()
    image=np.full((480,640,3),255,np.uint8)
    raw=board.generateImage((360,240))
    image[120:360,140:500]=cv2.cvtColor(raw,cv2.COLOR_GRAY2BGR)
    return image


class GeometryTests(unittest.TestCase):
    def test_position_report_keeps_failed_points_and_requires_two_heights(self):
        session=dict(real=True,point_checks=[])
        cfg=dict(max_position_error_mm=1)
        self.assertFalse(Calibration.position_report(session,cfg)['passed'])
        for kind in ('table','top'):
            for x,y in ((0,0),(50,0),(0,50)):
                session['point_checks'].append(dict(kind=kind,actual_xyz_mm=[x,y,0],error_mm=.5))
        self.assertTrue(Calibration.position_report(session,cfg)['passed'])
        session['point_checks'][0]['error_mm']=3
        report=Calibration.position_report(session,cfg)
        self.assertFalse(report['passed']);self.assertEqual(len(report['errors_mm']),6)
    def test_printed_board_definition_and_all_corners(self):
        d=g.detect(board_image())
        self.assertTrue(d['acceptable'],d)
        self.assertEqual(set(d['ids']),set(range(40)))
        self.assertEqual(set(d['marker_ids']),set(range(27)))

    def test_blank_and_blurred_images_not_accepted(self):
        self.assertFalse(g.detect(np.full((480,640,3),255,np.uint8))['acceptable'])
        self.assertFalse(g.detect(cv2.GaussianBlur(board_image(),(71,71),20))['acceptable'])

    def test_intrinsics_recovers_known_lens_with_heldout_frames(self):
        result=g.fit_intrinsics(intrinsic_rows())
        self.assertTrue(result['passed'],result)
        np.testing.assert_allclose(result['matrix'],K,atol=.02)
        self.assertEqual(len(result['per_view']),16)
        self.assertFalse(result['accuracy_verified'])

    def test_bad_heldout_frame_is_preserved_and_fails(self):
        rows=intrinsic_rows();rows[-1]['detection']['pixels'][0][0]+=20
        result=g.fit_intrinsics(rows)
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['per_view']),len(rows))
        np.testing.assert_allclose(result['matrix'],K,atol=.02)

    def test_duplicate_and_missing_holdout_fail(self):
        rows=intrinsic_rows();rows[-1]['jpeg_sha256']=rows[0]['jpeg_sha256']
        with self.assertRaisesRegex(ValueError,'重复'):g.fit_intrinsics(rows)
        with self.assertRaisesRegex(ValueError,'独立检查'):g.fit_intrinsics(intrinsic_rows()[:12])

    def test_insufficient_tilt_fails(self):
        rows=[projected_row(i+1,[0,0,0],[-90+i*3,-60,400], 'fit' if i<10 else 'check') for i in range(13)]
        self.assertFalse(g.fit_intrinsics(rows)['passed'])

    def test_hand_eye_recovers_link4_mount_and_fixed_board(self):
        rows,mount,board=hand_eye_rows()
        result=g.fit_hand_eye(rows,dict(matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480]))
        self.assertTrue(result['passed'],result)
        np.testing.assert_allclose(result['link4_to_camera_mm'],mount,atol=.02)
        np.testing.assert_allclose(result['base_to_board_mm'],board,atol=.02)

    def test_hand_eye_requires_two_rotation_directions(self):
        rows,_,_=hand_eye_rows()
        for row in rows:row['base_to_link4_mm']=np.eye(4).tolist()
        with self.assertRaisesRegex(ValueError,'不平行'):g.fit_hand_eye(rows,dict(matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480]))

    def test_moving_board_in_heldout_is_rejected_without_refit(self):
        rows,mount,_=hand_eye_rows();rows[-1]['base_to_link4_mm'][0][3]+=20
        result=g.fit_hand_eye(rows,dict(matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480]))
        self.assertFalse(result['passed'])
        np.testing.assert_allclose(result['link4_to_camera_mm'],mount,atol=.02)
        self.assertGreater(result['per_view'][-1]['translation_mm'],15)

    def test_hand_eye_accepts_8mm_but_rejects_12mm_without_refitting(self):
        for offset,passed in ((8,True),(12,False)):
            with self.subTest(offset=offset):
                rows,mount,_=hand_eye_rows()
                rows[-1]['base_to_link4_mm'][0][3]+=offset
                result=g.fit_hand_eye(rows,dict(matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480]))
                self.assertEqual(result['passed'],passed,result)
                self.assertEqual(result['consistency_limits'],dict(translation_mm=10,rotation_deg=2))
                np.testing.assert_allclose(result['link4_to_camera_mm'],mount,atol=.02)
                self.assertAlmostEqual(result['per_view'][-1]['translation_mm'],offset,places=2)

    def test_hand_eye_still_rejects_3degree_heldout_rotation(self):
        rows,mount,board=hand_eye_rows()
        robot=np.array(rows[-1]['base_to_link4_mm'])
        rotation=cv2.Rodrigues(np.array([0.,0.,np.deg2rad(3)]))[0]
        # Rotate the inferred board about its own origin, retaining its position.
        camera_board=np.linalg.inv(mount)@np.linalg.inv(robot)@board
        changed=board.copy();changed[:3,:3]=rotation@board[:3,:3]
        rows[-1]['base_to_link4_mm']=(changed@np.linalg.inv(camera_board)@np.linalg.inv(mount)).tolist()
        result=g.fit_hand_eye(rows,dict(matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480]))
        self.assertFalse(result['passed'])
        self.assertAlmostEqual(result['per_view'][-1]['rotation_deg'],3,places=2)
        self.assertLess(result['per_view'][-1]['translation_mm'],.02)


class SessionTests(unittest.TestCase):
    def test_ik_tolerance_change_keeps_projection_and_legacy_one_mm_binding(self):
        before=self.cal.projection_binding();legacy=[self.cal.projection_binding(1),self.cal.projection_binding(1.)]
        cfg=self.manipulation.load();cfg['max_position_error_mm']=8.
        self.manipulation.path.write_text(json.dumps(cfg))
        self.assertEqual(self.cal.projection_binding(),before)
        for binding in legacy:self.assertTrue(self.cal.projection_matches(binding))
        cfg['table_z_mm']+=1;self.manipulation.path.write_text(json.dumps(cfg))
        self.assertFalse(self.cal.projection_matches(before))
        for binding in legacy:self.assertFalse(self.cal.projection_matches(binding))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name in ('AGENTS.md','机械臂操作准则.md','arm_playground/pose_setup.py','arm_playground/joint_check.py','camera_preview/server.py'):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fake audit fixture')
        self.cfg,self.arms,_=fixture()
        self.arm_path=self.root/'arm_playground/config.json';self.arm_path.write_text(json.dumps(self.arms))
        config=self.root/'sorting_demo/config.json';config.parent.mkdir();config.write_text('{}')
        StatefulFake.values=self.cfg['observation_angles']+[80];StatefulFake.calls=[];StatefulFake.reads=0;StatefulFake.fail_read=None;StatefulFake.fail_write=None
        self.control=Control(self.arm_path,real=True,factory=StatefulFake,root=self.root,lock_factory=nullcontext)
        frame=board_image();jpeg=cv2.imencode('.jpg',frame)[1].tobytes()
        self.camera=SimpleNamespace(condition=threading.Condition(),jpeg=jpeg,sequence=1,updated=time.monotonic(),status=lambda:dict(connected=True))
        self.demo=SimpleNamespace(control=self.control,config_path=config,camera=self.camera,store=EventStore(self.root/'events.jsonl'))
        self.manipulation=Manipulation(self.demo);self.manipulation.path.write_text(json.dumps(self.cfg));self.cal=self.manipulation.calibration
        self.original_arms=self.arm_path.read_bytes()

    def tearDown(self):
        self.assertEqual(StatefulFake.calls,[],'calibration must never write motors')
        self.assertEqual(self.arm_path.read_bytes(),self.original_arms)
        self.tmp.cleanup()

    def fields(self,**kwargs):return dict(revision=self.cal.load()['revision'],**kwargs)
    def new(self,kind='intrinsics',**kwargs):
        return self.cal.new_session(self.fields(kind=kind,rulers_mm=[100,100],board_ready=True,**kwargs))['session_id']
    def capture(self,sid):
        self.camera.updated=time.monotonic()
        return self.cal.capture(self.fields(session_id=sid))['capture']

    def test_intrinsic_capture_does_not_read_servos(self):
        sid=self.new();r=self.capture(sid)
        self.assertEqual(StatefulFake.reads,0)
        self.assertIsNone(r['joints']);self.assertTrue(r['detection']['acceptable'])
        self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='fit'))
        with self.assertRaisesRegex(ValueError,'同一原图|分组'):self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='check'))

    def test_wrong_scale_and_stale_revision_rejected(self):
        with self.assertRaisesRegex(ValueError,'打印比例'):self.cal.new_session(self.fields(kind='intrinsics',rulers_mm=[98,100],board_ready=True))
        self.new()
        with self.assertRaisesRegex(ValueError,'已改变'):self.cal.new_session(dict(revision=0,kind='intrinsics'))

    def test_tampered_image_rejected_and_exclusion_preserves_original(self):
        sid=self.new();r=self.capture(sid)
        self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='fit'))
        self.cal.remove(self.fields(session_id=sid,capture_id=r['id']))
        self.assertTrue((self.cal.frames/(r['id']+'.jpg')).exists())
        (self.cal.frames/(r['id']+'.jpg')).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'原图已改变'):self.cal.image(r['id'])

    def test_current_robot_mode_and_control_busy_rejected(self):
        sid=self.new();self.control.real=False
        with self.assertRaisesRegex(ValueError,'混用'):self.capture(sid)
        self.control.real=True;self.control.busy=True
        with self.assertRaisesRegex(ValueError,'正在执行'):self.capture(sid)
        self.control.busy=False

    def test_hand_eye_requires_real_adopted_intrinsics(self):
        with self.assertRaisesRegex(ValueError,'先完成并采用'):self.new('hand_eye',board_fixed=True)
        with self.assertRaisesRegex(ValueError,'固定不动'):self.new('hand_eye')

    def test_image_identifier_does_not_allow_path_traversal(self):
        with self.assertRaises(ValueError):self.cal.image('../../arm_playground/config')

    def test_failed_detector_still_preserves_original(self):
        sid=self.new()
        with patch.object(self.cal,'detect',side_effect=ValueError('synthetic detector failure')):
            r=self.capture(sid)
        self.assertFalse(r['detection']['acceptable'])
        self.assertTrue(r['detection']['detector_failed'])
        self.assertEqual(self.cal.image(r['id']),self.camera.jpeg)
        with self.assertRaisesRegex(ValueError,'质量'):self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='fit'))

    def test_hand_eye_real_reads_are_complete_stable_and_never_retried(self):
        self.seed_solution()
        with patch.object(self.cal,'active_optics',return_value=('a'*32,{})):
            sid=self.new('hand_eye',board_fixed=True)
            r=self.capture(sid)
            self.assertEqual(StatefulFake.reads,2)
            self.assertEqual(len(r['joints']),6)
            self.assertIn('base_to_link4_mm',r)
            StatefulFake.fail_read=3
            with self.assertRaisesRegex(RuntimeError,'synthetic read failure'):self.capture(sid)
            self.assertEqual(StatefulFake.reads,3,'failed reads must not be retried')

    def test_changed_readback_rejects_capture_without_modifying_targets(self):
        self.seed_solution()
        rows=[dict(id=i,angle=v,read_ok=True,error=None) for i,v in enumerate(StatefulFake.values,1)]
        after=copy.deepcopy(rows);after[0]['angle']+=1
        with patch.object(self.cal,'active_optics',return_value=('a'*32,{})):
            sid=self.new('hand_eye',board_fixed=True)
            with patch.object(self.control,'read_rows',side_effect=[rows,after]) as reads:
                with self.assertRaisesRegex(ValueError,'读数变化'):self.capture(sid)
                self.assertEqual(reads.call_count,2)
        self.assertEqual(len(self.cal.load()['captures']),0)

    def test_excluded_check_cannot_be_relabelled_fit(self):
        sid=self.new();r=self.capture(sid)
        self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='check'))
        self.cal.remove(self.fields(session_id=sid,capture_id=r['id']))
        with self.assertRaisesRegex(ValueError,'分组已锁定'):self.cal.add(self.fields(session_id=sid,capture_id=r['id'],role='fit'))

    def test_full_session_fit_replays_original_metadata_and_hashes(self):
        sid=self.new();data=self.cal.load()
        for i,row in enumerate(intrinsic_rows()):
            identifier=row.pop('id');role=row.pop('role')
            raw_image=self.camera.jpeg+str(i).encode()
            meta=dict(row,id=identifier,session_id=sid,real=True,joints=None,
                jpeg_sha256=hashlib.sha256(raw_image).hexdigest(),robot_binding=self.cal.robot_binding())
            self.cal.frames.mkdir(parents=True,exist_ok=True)
            (self.cal.frames/(identifier+'.jpg')).write_bytes(raw_image)
            raw=json.dumps(meta).encode();(self.cal.frames/(identifier+'.json')).write_bytes(raw)
            data['captures'][identifier]=dict(metadata_sha256=hashlib.sha256(raw).hexdigest())
            data['sessions'][sid]['samples'].append(dict(capture_id=identifier,role=role,corners=40))
        self.cal.store(data,'synthetic_source_fixture')
        result=self.cal.solve(self.fields(session_id=sid))['result']
        self.assertTrue(result['passed'],result)
        self.assertEqual(len(result['evidence']),16)
        self.cal.solution(self.cal.load(),result['id'])
        original=self.cal.frames/(result['evidence'][0]['id']+'.jpg');original.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'原图已改变'):self.cal.solution(self.cal.load(),result['id'])

    def test_http_routes_same_origin_and_no_motor_requests(self):
        import io
        from server import demo_handler
        self.demo.manipulation=self.manipulation
        cls=demo_handler(self.demo)
        def post(path,fields,origin='http://localhost',host='localhost'):
            handler=object.__new__(cls);handler.path=path
            raw=json.dumps(fields).encode();handler.rfile=io.BytesIO(raw)
            handler.headers={'Content-Type':'application/json','Content-Length':str(len(raw)),'Origin':origin,'Host':host}
            replies=[];handler.reply=lambda code,mime,body:replies.append((code,json.loads(body)))
            handler.do_POST();return replies[-1]
        fields=dict(self.fields(kind='intrinsics',rulers_mm=[100,100],board_ready=True),csrf=self.control.csrf)
        self.assertEqual(post('/calibration/session',fields,origin='http://elsewhere')[0],403)
        bad=dict(fields,csrf='wrong');self.assertEqual(post('/calibration/session',bad)[0],403)
        code,result=post('/calibration/session',fields);self.assertEqual(code,200,result)
        sid=result['session_id']
        code,result=post('/calibration/capture',dict(self.fields(session_id=sid),csrf=self.control.csrf))
        self.assertEqual(code,200,result)
        self.assertEqual(StatefulFake.reads,0)

    def seed_solution(self,kind='intrinsics',real=True,passed=True):
        data=self.cal.load()
        result=dict(id='a'*32,kind=kind,real=real,passed=passed,board=g.BOARD,evidence=[],
            solver_sha256=hashlib.sha256((Path(g.__file__)).read_bytes()).hexdigest(),
            matrix=K.tolist(),distortion=D.tolist(),image_size=[640,480],robot_binding=self.cal.robot_binding())
        result['certificate']=g.digest(result);data['solutions'][result['id']]=result
        self.cal.store(data,'synthetic_test_fixture')
        return result

    def test_apply_rejects_simulation_failure_tampering_and_no_confirmation(self):
        for real,passed in [(False,True),(True,False)]:
            r=self.seed_solution(real=real,passed=passed)
            with self.assertRaisesRegex(ValueError,'真实采样'):self.cal.apply(self.fields(solution_id=r['id'],confirmed=True))
        r=self.seed_solution()
        with self.assertRaisesRegex(ValueError,'明确确认'):self.cal.apply(self.fields(solution_id=r['id']))
        data=self.cal.load();data['solutions'][r['id']]['matrix'][0][0]=1000;self.cal.store(data,'synthetic_tamper')
        with self.assertRaisesRegex(ValueError,'被修改'):self.cal.apply(self.fields(solution_id=r['id'],confirmed=True))

    def test_tolerance_update_preserves_only_known_predecessor_intrinsics(self):
        predecessor='2d7441047b4d5be9b269a7a623ad1131c41886bcfda15e398c0163a5b7cd3ca0'
        for kind,solver,allowed in [('intrinsics',predecessor,True),('hand_eye',predecessor,False),('intrinsics','0'*64,False)]:
            with self.subTest(kind=kind,solver=solver):
                result=self.seed_solution(kind=kind)
                result.pop('certificate');result['solver_sha256']=solver;result['certificate']=g.digest(result)
                data=self.cal.load();data['solutions'][result['id']]=result
                if allowed:
                    self.assertEqual(self.cal.solution(data,result['id']),result)
                    damaged=copy.deepcopy(data);damaged['solutions'][result['id']]['matrix'][0][0]+=1
                    with self.assertRaisesRegex(ValueError,'被修改'):self.cal.solution(damaged,result['id'])
                else:
                    with self.assertRaisesRegex(ValueError,'版本已改变'):self.cal.solution(data,result['id'])

    def test_apply_optics_does_not_set_calibration_acceptance_or_change_limits(self):
        cfg,_,meta,_=case();meta['real']=True
        self.manipulation.path.write_text(json.dumps(cfg))
        self.manipulation.frames.mkdir(parents=True,exist_ok=True)
        raw=b'fake preserved anchor';meta['jpeg_sha256']=hashlib.sha256(raw).hexdigest()
        (self.manipulation.frames/(meta['id']+'.jpg')).write_bytes(raw)
        (self.manipulation.frames/(meta['id']+'.json')).write_text(json.dumps(meta))
        r=self.seed_solution()
        self.control.pending={'token':'old'}
        self.cal.apply(self.fields(solution_id=r['id'],confirmed=True,setup_sha256=self.manipulation.sha()))
        adopted=self.manipulation.load()
        self.assertIsNone(self.control.pending)
        self.assertFalse(adopted['calibration_reviewed']);self.assertFalse(adopted['model_reviewed'])
        self.assertEqual(adopted['cad']['camera_calibration']['intrinsics'],r['id'])
        self.assertEqual(adopted['camera']['matrix'],K.tolist())
        self.cal.verify_applied(adopted)
        adopted['cad']['intrinsics_recipe']['matrix'][0][0]=111
        with self.assertRaisesRegex(ValueError,'来源'):self.cal.verify_applied(adopted)

    def test_exclude_typo_and_adopt_table_scope_without_relaxing_motion(self):
        from manipulation_geometry import project_to_plane
        self.test_apply_hand_eye_table_height_and_regeneration_preserve_sources()
        sid=self.new('position');cfg=self.manipulation.load()
        for i,px in enumerate(([280,240],[360,240],[320,300])):
            shot=self.capture(sid)
            predicted=project_to_plane(cfg,px,cfg['table_z_mm'],[r['angle'] for r in shot['joints'][:5]])
            actual=predicted+np.array(([1,0,0],[0,2,0],[3,2,0])[i])
            if i==2:
                self.cal.point_check(self.fields(session_id=sid,capture_id=shot['id'],pixel=px,
                    actual_xyz_mm=(actual+[0,-20,0]).tolist(),kind='table',measured_independently=True))
            self.cal.point_check(self.fields(session_id=sid,capture_id=shot['id'],pixel=px,
                actual_xyz_mm=actual.tolist(),kind='table',measured_independently=True))
        points=copy.deepcopy(self.cal.load()['sessions'][sid]['point_checks']);bad=points[-2]
        self.assertEqual(bad['capture_id'],points[-1]['capture_id'])
        with self.assertRaisesRegex(ValueError,'原因'):
            self.cal.exclude_point(self.fields(session_id=sid,record_sha256=g.digest(bad)))
        self.cal.exclude_point(self.fields(session_id=sid,record_sha256=g.digest(bad),reason='实测Y录错，下一条已更正'))
        session=self.cal.load()['sessions'][sid]
        self.assertEqual(session['point_checks'],points[:2]+points[-1:])
        self.assertEqual(session['excluded_point_checks'][0]['record'],bad)
        with self.assertRaisesRegex(ValueError,'唯一定位'):
            self.cal.exclude_point(self.fields(session_id=sid,record_sha256=g.digest(bad),reason='重复操作'))
        before=self.manipulation.path.read_bytes()
        status=self.cal.adopt_position(self.fields(session_id=sid,limit_mm=5,scope='table',confirmed=True))
        self.assertEqual(self.manipulation.path.read_bytes(),before)
        result=status['position_result'];self.assertTrue(result['valid']);self.assertEqual(result['point_count'],3)
        self.assertEqual(result['scope'],'table');self.assertEqual(result['limit_mm'],5);self.assertFalse(result['grasp_verified'])
        report=next(s['report'] for s in status['sessions'] if s['id']==sid)
        self.assertTrue(report['table_passed']);self.assertFalse(report['top_passed']);self.assertFalse(report['passed'])
        self.assertEqual(self.manipulation.load()['max_position_error_mm'],cfg['max_position_error_mm'])
        with self.assertRaisesRegex(ValueError,'尚未通过'):
            self.cal.adopt_position(self.fields(session_id=sid,limit_mm=5,scope='table_and_top',confirmed=True))
        with self.assertRaisesRegex(ValueError,'有限'):
            self.cal.adopt_position(self.fields(session_id=sid,limit_mm=True,scope='table',confirmed=True))
        self.cal.exclude_point(self.fields(session_id=sid,record_sha256=g.digest(points[0]),reason='测试采用后证据变化'))
        self.assertFalse(self.cal.status()['position_result']['valid'])

    def test_independent_table_datum_preserves_points_and_survives_regeneration(self):
        self.test_apply_hand_eye_table_height_and_regeneration_preserve_sources()
        old_sid=self.new('position');shot=self.capture(old_sid)
        self.cal.point_check(self.fields(session_id=old_sid,capture_id=shot['id'],pixel=[320,240],
            actual_xyz_mm=[0,200,-17],kind='table',measured_independently=True))
        old_points=copy.deepcopy(self.cal.load()['sessions'][old_sid]['point_checks'])
        mount=copy.deepcopy(self.manipulation.load()['cad']['camera_mount'])
        for bad in (True,float('nan'),None):
            with self.assertRaisesRegex(ValueError,'实测高度'):
                self.cal.set_table_height(self.fields(confirmed=True,measured_height_mm=bad,
                    work_surface='灰色桌面',setup_sha256=self.manipulation.sha()))
        self.control.pending={'token':'old'};self.manipulation.drafts['old']={}
        status=self.cal.set_table_height(self.fields(confirmed=True,measured_height_mm=80,
            work_surface='灰色桌面（白色设备箱下方），蓝圈基准面实测80毫米',photo_sha256='f'*64,
            setup_sha256=self.manipulation.sha()))
        cfg=self.manipulation.load();source=cfg['cad']['camera_calibration']['table_height_source']
        self.assertEqual(cfg['table_z_mm'],0);self.assertEqual(cfg['cad']['camera_mount'],mount)
        self.assertEqual(source['kind'],'measured_model_datum');self.assertEqual(status['table_z_mm'],0)
        self.assertFalse(cfg['calibration_reviewed']);self.assertIsNone(self.control.pending)
        self.assertEqual(self.manipulation.drafts,{})
        self.assertEqual(self.cal.load()['sessions'][old_sid]['point_checks'],old_points)
        with self.assertRaisesRegex(ValueError,'定位参数已改变'):self.capture(old_sid)
        self.new('position')
        corrupt=copy.deepcopy(cfg);corrupt['table_z_mm']=-16
        with self.assertRaisesRegex(ValueError,'独立实测基准'):self.cal.verify_applied(corrupt)
        before=self.manipulation.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'配置已改变'):
            self.cal.set_table_height(self.fields(confirmed=True,measured_height_mm=80,work_surface='灰色桌面',setup_sha256='wrong'))
        self.assertEqual(self.manipulation.path.read_bytes(),before)
        self.cal.apply(self.fields(solution_id='c'*32,confirmed=True,setup_sha256=self.manipulation.sha(),board_on_table=False))
        self.manipulation.prepare_cad(dict(frame_id='a'*32,capture_mode='joint_snapshot_v1',
            intrinsics_recipe=dict(kind='fov_estimate',fov_deg=110,axis='diagonal'),expected_sha256=self.manipulation.sha()))
        cfg=self.manipulation.load();self.assertEqual(cfg['table_z_mm'],0)
        self.assertEqual(cfg['cad']['camera_calibration']['table_height_source'],source)
        self.cal.verify_applied(cfg)

    def test_apply_hand_eye_table_height_and_regeneration_preserve_sources(self):
        cfg,_,meta,_=case();meta['real']=True
        self.manipulation.path.write_text(json.dumps(cfg))
        self.manipulation.frames.mkdir(parents=True,exist_ok=True)
        raw=b'fake preserved anchor';meta['jpeg_sha256']=hashlib.sha256(raw).hexdigest()
        (self.manipulation.frames/(meta['id']+'.jpg')).write_bytes(raw)
        (self.manipulation.frames/(meta['id']+'.json')).write_text(json.dumps(meta))
        optics=self.seed_solution()
        self.cal.apply(self.fields(solution_id=optics['id'],confirmed=True,setup_sha256=self.manipulation.sha()))
        data=self.cal.load();result=dict(id='b'*32,kind='hand_eye',passed=True,real=True,board=g.BOARD,evidence=[],
            optics_id=optics['id'],robot_binding=self.cal.robot_binding(),
            solver_sha256=hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest(),
            camera_mount=cfg['cad']['camera_mount'],base_to_board_mm=g.transform(np.diag([1,-1,-1]),[0,200,-12]).tolist())
        result['certificate']=g.digest(result);data['solutions'][result['id']]=result;self.cal.store(data,'synthetic_hand_eye_fixture')
        self.cal.apply(self.fields(solution_id=result['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_on_table=True,board_thickness_mm=5))
        adopted=self.manipulation.load();self.assertEqual(adopted['table_z_mm'],-17)
        self.assertEqual(adopted['cad']['camera_calibration']['table_height_source']['board_corner_spread_mm'],0)
        self.assertFalse(adopted['calibration_reviewed'])
        self.manipulation.prepare_cad(dict(frame_id=meta['id'],capture_mode='joint_snapshot_v1',
            intrinsics_recipe=dict(kind='fov_estimate',fov_deg=110,axis='diagonal'),expected_sha256=self.manipulation.sha()))
        regenerated=self.manipulation.load()
        self.assertEqual(regenerated['cad']['camera_calibration']['hand_eye'],result['id'])
        self.assertEqual(regenerated['camera']['matrix'],K.tolist());self.assertEqual(regenerated['table_z_mm'],-17)
        corrupted=copy.deepcopy(regenerated);corrupted['table_z_mm']=-16
        with self.assertRaisesRegex(ValueError,'板面估计'):self.cal.verify_applied(corrupted)

        # A tilted inferred board only blocks the optional height update, not
        # adopting a passed camera mount while retaining the existing table Z.
        data=self.cal.load();tilted=copy.deepcopy(data['solutions'][result['id']]);tilted.pop('certificate')
        tilted['id']='c'*32
        tilted['base_to_board_mm']=g.transform(cv2.Rodrigues(np.array([.1,0,0]))[0],[0,200,-12]).tolist()
        tilted['certificate']=g.digest(tilted);data['solutions'][tilted['id']]=tilted
        self.cal.store(data,'synthetic_tilted_board_fixture')
        original=self.manipulation.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'超过10毫米'):
            self.cal.apply(self.fields(solution_id=tilted['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_on_table=True,board_thickness_mm=5))
        self.assertEqual(self.manipulation.path.read_bytes(),original)
        self.cal.apply(self.fields(solution_id=tilted['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_on_table=False))
        adopted=self.manipulation.load()
        self.assertEqual(adopted['table_z_mm'],-17)
        self.assertEqual(adopted['cad']['camera_calibration']['hand_eye'],tilted['id'])
        self.assertNotIn('table_height_source',adopted['cad']['camera_calibration'])
        self.cal.verify_applied(adopted)

        # 用户10-29：约6毫米四角高差（与现场6.4毫米相近）可采用为桌面来源并记录。
        data=self.cal.load();mild=copy.deepcopy(data['solutions'][result['id']]);mild.pop('certificate')
        mild['id']='d'*32
        mild['base_to_board_mm']=g.transform(cv2.Rodrigues(np.array([.05,0,0]))[0],[0,200,-12]).tolist()
        mild['certificate']=g.digest(mild);data['solutions'][mild['id']]=mild
        self.cal.store(data,'synthetic_mild_tilt_fixture')
        self.cal.apply(self.fields(solution_id=mild['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_on_table=True,board_thickness_mm=3))
        adopted=self.manipulation.load();source=adopted['cad']['camera_calibration']['table_height_source']
        pose=np.array(mild['base_to_board_mm']);corners=np.array([[0,0,0,1],[180,0,0,1],[0,120,0,1],[180,120,0,1]])@pose.T
        self.assertAlmostEqual(adopted['table_z_mm'],float(np.mean(corners[:,2]))-3)
        self.assertGreater(source['board_corner_spread_mm'],5);self.assertLess(source['board_corner_spread_mm'],10)
        self.cal.verify_applied(adopted)

        # 用户10-29第三次：板面仅用于图像换算，运动桌面高度保持不变。
        before=adopted['table_z_mm']
        self.cal.apply(self.fields(solution_id=mild['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_projection_only=True,board_thickness_mm=3))
        adopted=self.manipulation.load();ref=adopted['cad']['camera_calibration']
        self.assertEqual(adopted['table_z_mm'],before)
        self.assertAlmostEqual(ref['projection_table']['z_mm'],float(np.mean(corners[:,2]))-3)
        self.cal.verify_applied(adopted)
        tampered=copy.deepcopy(adopted);tampered['cad']['camera_calibration']['projection_table']['z_mm']+=1
        with self.assertRaisesRegex(ValueError,'投影板面'):self.cal.verify_applied(tampered)
        with self.assertRaisesRegex(ValueError,'二选一'):
            self.cal.apply(self.fields(solution_id=mild['id'],confirmed=True,setup_sha256=self.manipulation.sha(),board_projection_only=True,board_on_table=True,board_thickness_mm=3))


if __name__=='__main__':unittest.main()
