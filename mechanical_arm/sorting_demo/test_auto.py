"""仅合成相机、官方模型数学计算和假设备；所有尺寸是测试夹具，禁止部署。"""
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
from auto_geometry import MODEL, Robot, pixel_to_base, calibration_report, segment_distance, segment_box_distance, find_top
from auto_plan import default_setup, readiness, build_plan, arm_fingerprint
from auto_task import AutoTask
from control import Control
from core import EventStore
from test_control import Fake


def fixture():
    cfg=default_setup()
    cfg.update(table_z_mm=0,block_size_mm=[30,30,30],jaw_open_width_mm=70,max_position_error_mm=1,
               grasp_height_mm=15,transfer_height_mm=100,release_gap_mm=5,yellow_xy_mm=[50,200],
               observation_angles=[90,50,32,82,90],observation_readings=[90,49,32,82,90],
               grasp_reference_angles=[90,30,30,30,90],
               model_reviewed=True,calibration_reviewed=True,path_reviewed=True)
    cfg['camera'].update(matrix=[[800,0,320],[0,800,240],[0,0,1]],distortion=[0]*5)
    cfg['marks']['xy_mm']=[[-100,100],[100,100],[100,300],[-100,300]]
    def project(xy,z=0):
        # 独立的已知相机：位于(0,200,500)，向下看。
        return [320+800*xy[0]/(500-z),240-800*(xy[1]-200)/(500-z)]
    cfg['marks']['pixels']=[project(xy) for xy in cfg['marks']['xy_mm']]
    cfg['check_point']=dict(xy_mm=[30,230],pixel=project([30,230]))
    cfg['height_checks']=[dict(xy_mm=xy,pixel=project(xy,30)) for xy in [[-50,150],[50,150],[0,260]]]
    cfg['model'].update(tcp_xyz_mm=[0,0,120],tcp_rpy_deg=[0,0,0],collision_radii_mm=[.1]*5,
                        tool_radius_mm=.1,tool_boxes_mm=[dict(center=[0,0,60],half_size=[.1,.1,.1])])
    arms=dict(hardware_ready=True,motion_ms=1500,settle_ms=300,
              joint_limits={str(i):[90,250] if i==5 else [10,170] for i in range(1,7)},
              gripper=dict(open=80,closed=129),poses={},targets={})
    robot=Robot(cfg,arms)
    cfg['model']['measurements']=[dict(angles=q,xyz_mm=(robot.fk(q)[0][:3,3]*1000).tolist())
                    for q in [[90,50,32,82,90],[95,55,32,82,90],[85,45,40,80,90]]]
    cfg['arm_config_sha256']=arm_fingerprint(arms)
    return cfg,arms


class GeometryTests(unittest.TestCase):
    def test_height_and_distortion_projection(self):
        cfg,_=fixture()
        point=[35,235];pixel=[320+800*35/470,240-800*35/470]
        actual=pixel_to_base(cfg,pixel,30)
        np.testing.assert_allclose(actual,[35,235,30],atol=.001)
        incorrect=pixel_to_base(cfg,pixel,0)
        self.assertGreater(np.linalg.norm(incorrect[:2]-point),2)
        cfg['camera']['distortion']=[-.1,.02,.001,.001,0]
        rotation=np.array([np.pi,0,0]);translation=np.array([0,200,500.],float)
        markers=np.column_stack([cfg['marks']['xy_mm'],np.zeros(4)]).astype(float)
        cfg['marks']['pixels']=cv2.projectPoints(markers,rotation,translation,np.asarray(cfg['camera']['matrix'],float),np.asarray(cfg['camera']['distortion']))[0].reshape(4,2).tolist()
        image=cv2.projectPoints(np.array([[35,235,30.]],float),rotation,translation,np.asarray(cfg['camera']['matrix'],float),np.asarray(cfg['camera']['distortion']))[0].reshape(2)
        np.testing.assert_allclose(pixel_to_base(cfg,image.tolist(),30),[35,235,30],atol=.002)

    def test_independent_calibration_and_height_checks(self):
        cfg,arms=fixture();report=calibration_report(cfg)
        self.assertLess(max(report['height_errors_mm']),.001)
        self.assertTrue(readiness(cfg,arms)['ready'])
        cfg['height_checks'][0]['pixel'][0]+=10
        with self.assertRaises(ValueError):calibration_report(cfg)

    def test_degenerate_wrong_limits_and_missing_setup(self):
        cfg,_=fixture();cfg['marks']['xy_mm']=[[0,0],[1,0],[2,0],[3,0]]
        with self.assertRaises(ValueError):calibration_report(cfg)
        _,arms=fixture();self.assertFalse(readiness(default_setup(),arms)['ready'])
        cfg,arms=fixture();arms['joint_limits']['5']=[89,250]
        self.assertFalse(readiness(cfg,arms)['ready'])

    def test_model_fk_uses_actual_course_offsets(self):
        cfg,arms=fixture();robot=Robot(cfg,arms)
        matrix,_=robot.fk([90]*5)
        np.testing.assert_allclose(matrix[:3,3]*1000,[0,-2.15,467.05],atol=.002)
        cfg['model']['urdf_sha256']='wrong'
        with self.assertRaises(ValueError):Robot(cfg,arms)

    def test_ik_roundtrip_and_unreachable(self):
        cfg,arms=fixture();robot=Robot(cfg,arms);q=[90,30,30,30,90]
        matrix,_=robot.fk(q)
        np.testing.assert_allclose(matrix[:3,2],[0,0,-1],atol=.00002)
        solution=robot.ik((matrix[:3,3]*1000).tolist(),[90,32,29,29,90])
        self.assertLess(np.linalg.norm(robot.fk(solution)[0][:3,3]-matrix[:3,3])*1000,1)
        with self.assertRaises(ValueError):robot.ik([1000,1000,1000],q)

    def test_capsule_and_box_distances(self):
        a=np.array([0.,0,0]);b=np.array([1.,0,0]);c=np.array([0.,2,0]);d=np.array([1.,2,0])
        self.assertAlmostEqual(segment_distance(a,b,c,d),2)
        self.assertEqual(segment_distance(a,b,np.array([.5,-1,0]),np.array([.5,1,0])),0)
        self.assertAlmostEqual(segment_box_distance(np.array([-3.,2,0]),np.array([3.,2,0]),np.ones(3)),1)
        self.assertEqual(segment_box_distance(np.array([-3.,0,0]),np.array([3.,0,0]),np.ones(3)),0)

    def test_table_collision_and_no_angle_clipping(self):
        cfg,arms=fixture();cfg['marks']['xy_mm']=[[-100,100],[100,100],[100,400],[-100,400]]
        robot=Robot(cfg,arms);robot.collision([90,50,32,82,90])
        cfg['table_z_mm']=1000
        with self.assertRaises(ValueError):Robot(cfg,arms).collision([90]*5)
        with self.assertRaises(ValueError):robot.transition([90,50,32,82,90],[90,50,32,82,89])

    def test_known_square_top_and_ambiguous_rejection(self):
        cfg,_=fixture();frame=np.zeros((480,640,3),np.uint8)
        # 30毫米顶面在z=30平面，完整投影边长约51像素。
        cv2.rectangle(frame,(294,214),(345,265),(0,255,255),-1)
        vision=dict(roi=[0,0,640,480],min_area=100,colors=dict(yellow=[[20,80,60],[38,255,255]]))
        target=find_top(frame,cfg,vision)
        np.testing.assert_allclose(target['xyz_mm'],[0,200,30],atol=1)
        cv2.rectangle(frame,(400,200),(451,251),(0,255,255),-1)
        with self.assertRaises(ValueError):find_top(frame,cfg,vision)

    def test_plan_preflight_rejects_missing_claw_and_review(self):
        cfg,arms=fixture();arms['gripper']['closed']=None
        with self.assertRaises(ValueError):build_plan(cfg,arms,[0,200,30])

    def test_complete_plan_from_reachable_model_fixture(self):
        cfg,arms=fixture();robot=Robot(cfg,arms)
        pick_pose=[90,34,32,82,90]
        point=robot.fk(pick_pose)[0][:3,3]*1000
        cfg['table_z_mm']=float(point[2]-15)
        cfg['observation_angles']=[90,50,32,82,90]
        cfg['grasp_reference_angles']=pick_pose[:]
        cfg['transfer_height_mm']=51
        cfg['max_position_error_mm']=2
        cfg['camera']['matrix']=[[600,0,320],[0,600,240],[0,0,1]]
        cfg['marks']['xy_mm']=[[-100,100],[100,100],[100,400],[-100,400]]
        cfg['marks']['pixels']=[[200,360],[440,360],[440,0],[200,0]]
        for entry in [cfg['check_point']]+cfg['height_checks']:
            entry['pixel']=(np.array([320,240])+.75*(np.asarray(entry['pixel'])-[320,240])).tolist()
        cfg['observation_readings']=cfg['observation_angles'][:]
        cfg['yellow_xy_mm']=[float(point[0]+10),float(point[1])]
        plan=build_plan(cfg,arms,[float(point[0]),float(point[1]),cfg['table_z_mm']+30])
        self.assertGreater(len(plan['steps']),8)
        self.assertLessEqual(len(plan['steps']),100)
        for step in plan['steps']:
            self.assertEqual(len(step['joints']),1)
            joint,value=step['joints'][0]
            self.assertEqual(type(value),int)
        cfg,arms=fixture();cfg['path_reviewed']=False
        with self.assertRaises(ValueError):build_plan(cfg,arms,[0,200,30])


class AutoBusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for directory in ['arm_playground','camera_preview','sorting_demo']:(self.root/directory).mkdir()
        for name in ['AGENTS.md','机械臂操作准则.md','arm_playground/pose_setup.py','arm_playground/joint_check.py','camera_preview/server.py']:
            (self.root/name).write_text('test audit fixture')
        self.cfg,self.arms=fixture();self.path=self.root/'arm_playground/config.json';self.path.write_text(json.dumps(self.arms))
        self.visionpath=self.root/'sorting_demo/config.json';self.visionpath.write_text('{}')
        Fake.calls=[];Fake.fail=False;Fake.read_fail=False;Fake.wait=None;Fake.angles=[90,49,32,82,90,80]
        self.control=Control(self.path,real=True,factory=Fake,root=self.root,lock_factory=__import__('contextlib').nullcontext)
        self.demo=SimpleNamespace(control=self.control,config_path=self.visionpath,store=EventStore(self.root/'events.jsonl'))
        self.auto=AutoTask(self.demo,planner=lambda *a:None)

    def tearDown(self):
        for _ in range(200):
            if not self.control.busy:break
            time.sleep(.005)
        self.tmp.cleanup()

    def test_default_is_disabled_and_no_hardware_initialization(self):
        self.assertFalse(self.auto.status()['readiness']['ready']);self.assertEqual(Fake.calls,[])
        with self.assertRaises(ValueError):self.auto.preview()
        self.assertEqual(Fake.calls,[])

    def test_save_resets_reviews_and_preserves_backup(self):
        cfg=default_setup();cfg['table_z_mm']=25;cfg['path_reviewed']=True
        result=self.auto.save(dict(setup=cfg,expected_sha256=None))
        self.assertFalse(result['status']['setup']['path_reviewed'])
        before=self.auto.path.read_bytes();cfg=result['status']['setup'];cfg['table_z_mm']=26
        self.auto.save(dict(setup=cfg,expected_sha256=self.auto.digest()))
        backups=list(self.auto.path.parent.glob('auto_setup.before_*.json'))
        self.assertEqual(backups[0].read_bytes(),before)
        with self.assertRaises(ValueError):self.auto.save(dict(setup=cfg,expected_sha256='old'))
        self.assertEqual(Fake.calls,[])

    def test_reviews_require_real_evidence(self):
        self.auto.path.write_text(json.dumps(self.cfg));self.cfg['path_reviewed']=False
        with self.assertRaises(ValueError):self.auto.save(dict(setup=self.cfg,expected_sha256=self.auto.digest()))

    def test_snapshot_path_traversal_and_intrinsic_sample_guard(self):
        with self.assertRaises(ValueError):self.auto.snapshot_metadata('../../secret')
        with self.assertRaises(ValueError):self.auto.intrinsics(dict(xy_mm=self.cfg['marks']['xy_mm'],samples=[]))

    def test_auto_execution_error_has_no_later_or_cleanup_writes(self):
        plan=dict(steps=[dict(label='a',joints=[[1,100]],motion_ms=1500,settle_ms=300),
                         dict(label='b',joints=[[2,55]],motion_ms=1500,settle_ms=300)])
        args=dict(operation='auto',plan=plan)
        audit=self.control.audit(args)
        p=dict(token='test',parameters=args,audit=audit)
        with patch.object(self.auto,'validate_execution'),patch.object(self.control,'read_rows',return_value=[]):
            Fake.fail=True
            with self.assertRaises(RuntimeError):self.auto.run(Fake(self.arms),p)
        self.assertEqual(Fake.calls,[[(1,100)]])
        events,_=self.demo.store.latest();self.assertEqual(events[0]['result'],'failed')

    def test_batch_preflight_does_not_partially_send(self):
        plan=dict(steps=[dict(label='a',joints=[[1,100]],motion_ms=1500,settle_ms=300),
                         dict(label='b',joints=[[5,89]],motion_ms=1500,settle_ms=300)])
        with patch.object(self.auto,'validate_execution'):
            with self.assertRaises(ValueError):self.auto.run(Fake(self.arms),dict(token='test',parameters=dict(plan=plan)))
        self.assertEqual(Fake.calls,[])

    def test_auto_batch_confirmation_deduplicates_and_records_result(self):
        class Updating(Fake):
            def write(self,pairs):
                super().write(pairs)
                for joint,value in pairs:Fake.angles[joint-1]=value
        self.control.factory=Updating
        plan=dict(steps=[dict(label='one',joints=[[1,100]],motion_ms=1500,settle_ms=300),
                         dict(label='two',joints=[[2,55]],motion_ms=1500,settle_ms=300)])
        args=dict(operation='auto',plan=plan)
        audit=self.control.audit(args)
        rows=Updating(self.arms).read_report()
        p=dict(token='finite-test',expires=time.monotonic()+120,parameters=args,audit=audit,rows=rows,
               config_bytes=self.path.read_bytes())
        self.control.pending=p
        with patch.object(self.auto,'validate_execution'):
            with self.assertRaises(ValueError):self.control.execute(dict(token=p['token'],confirmed=False))
            self.control.execute(dict(token=p['token'],confirmed=True))
            for _ in range(500):
                if not self.control.busy:break
                time.sleep(.002)
            self.control.execute(dict(token=p['token'],confirmed=True))
        self.assertEqual(Fake.calls,[[(1,100)],[(2,55)]])
        self.assertEqual(self.control.job['status'],'completed')
        result=self.auto.result(dict(task_id=p['token'],result='success'))
        self.assertEqual(result['result'],'success')
        self.assertEqual(self.auto.result(dict(task_id=p['token'],result='failed'))['result'],'success')

    def test_changed_target_rejects_confirmation_before_write(self):
        args=dict(operation='auto',plan=dict(steps=[]))
        p=dict(token='changed',expires=time.monotonic()+120,parameters=args,audit=self.control.audit(args))
        self.control.pending=p
        with patch.object(self.auto,'validate_execution',side_effect=ValueError('changed target')):
            with self.assertRaises(ValueError):self.control.execute(dict(token='changed',confirmed=True))
        self.assertEqual(Fake.calls,[])


if __name__=='__main__':unittest.main()
