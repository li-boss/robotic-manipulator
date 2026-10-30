"""Nine-point reuse: synthetic data and fake hardware; no network or real servo access."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cv2
import numpy as np

import test_cad_manipulation as cad_tests
case=cad_tests.case
target_at=cad_tests.target_at
from test_manipulation import StatefulFake
from manipulation_geometry import ManipulationRobot,build_plan,project_to_plane,camera_pose,calibration_version
from manipulation_reference import build_reference,reference_seed,validate_reference
from manipulation_ik import batch_fk,candidates


def teaching_fixture(cfg):
    points=np.array([[x,y,cfg['table_z_mm']+30] for y in (245,260,275) for x in (-20,0,20)],float)
    k,d,r,t=camera_pose(cfg,cfg['observation_readings'])
    pixels=cv2.projectPoints(points,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
    rows=[dict(name='synthetic-'+str(i),slot=i+1,pixel=p.tolist(),above=[90,45,35,55,90],grasp=[90,45,35,55,90]) for i,p in enumerate(pixels)]
    return dict(mapping_mode='reported_cards',baseline=dict(frame_id='b'*32,readings=cfg['observation_readings'].copy(),image_size=[640,480]),
        reported_mapping=dict(operator_report=True,source_type='user_report',cards_sha256='c'*64,states_sha256='d'*64,samples=rows,
            fit=dict(cells=[dict(indices=x) for x in ([0,1,3],[1,3,4],[1,2,4],[2,4,5],[3,4,6],[4,6,7],[4,5,7],[5,7,8])])),
        source_type='SYNTHETIC TEST ONLY')


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.cfg,self.arms,_,self.target=case();self.teaching=teaching_fixture(self.cfg)
        self.reference=build_reference(self.cfg,self.arms,self.teaching)

    def test_old_pixels_are_converted_with_old_real_pose(self):
        for row,saved in zip(self.reference['samples'],self.teaching['reported_mapping']['samples']):
            expected=project_to_plane(self.cfg,saved['pixel'],self.cfg['table_z_mm']+30,self.teaching['baseline']['readings'])
            np.testing.assert_allclose(row['xyz_mm'],expected,atol=1e-9)
        changed=copy.deepcopy(self.teaching);changed['baseline']['readings'][1]+=5
        newer=build_reference(self.cfg,self.arms,changed)
        self.assertNotEqual(newer['version'],self.reference['version'])
        self.assertNotEqual(newer['samples'][0]['xyz_mm'],self.reference['samples'][0]['xyz_mm'])

    def test_convex_interpolation_and_outside_nearest_not_extrapolation(self):
        inside=reference_seed(self.reference,[0,260],'grasp');self.assertEqual(inside['mode'],'interpolated')
        self.assertTrue(all(-1e-10<=w<=1+1e-10 for w in inside['weights']))
        outside=reference_seed(self.reference,[200,500],'grasp');self.assertEqual(outside['mode'],'nearest')
        self.assertIn(outside['angles'],[r['grasp'] for r in self.reference['samples']])

    def test_changed_projection_or_reference_payload_is_rejected(self):
        bad=copy.deepcopy(self.reference);bad['samples'][0]['grasp'][0]+=1
        with self.assertRaisesRegex(ValueError,'指纹'):validate_reference(bad,self.cfg,self.arms)
        changed=copy.deepcopy(self.cfg);changed['table_z_mm']+=1
        with self.assertRaisesRegex(ValueError,'标定/模型'):validate_reference(self.reference,changed,self.arms)
        self.assertEqual(calibration_version(self.cfg),self.reference['projection_version'])

    def test_integer_hard_limits_and_resolution_not_silently_fixed(self):
        for q in (89,271,90.5):
            data=copy.deepcopy(self.teaching);data['reported_mapping']['samples'][0]['grasp'][4]=q
            with self.assertRaises(ValueError):build_reference(self.cfg,self.arms,data)
        changed=copy.deepcopy(self.teaching);changed['baseline']['image_size']=[1280,720]
        with self.assertRaises(ValueError):build_reference(self.cfg,self.arms,changed)

    def test_batch_fk_matches_strict_model_evaluation(self):
        robot=ManipulationRobot(self.cfg,self.arms);rng=np.random.default_rng(830)
        qs=[[int(rng.integers(lo,hi+1)) for lo,hi in (self.arms['joint_limits'][str(i)] for i in range(1,6))] for _ in range(120)]
        actual=batch_fk(robot,qs)
        for q,m in zip(qs,actual):np.testing.assert_allclose(m,robot.fk(q)[0],atol=2e-15)

    def test_fast_candidates_keep_original_position_direction_and_integer_limits(self):
        robot=ManipulationRobot(self.cfg,self.arms);xyz=[0,260,self.cfg['table_z_mm']+self.cfg['grasp_height_mm']]
        rows=candidates(robot,xyz,0,45,[90,45,35,55,90],[90,80,35,10,90])
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(all(type(v)is int for v in row['q']))
            m,_=robot.fk(row['q']);self.assertLessEqual(np.linalg.norm(m[:3,3]*1000-xyz),self.cfg['max_position_error_mm'])
            jaw=(m[:3,:3]@robot.jaw_axis)[:2];angle=np.degrees(np.arctan2(jaw[1],jaw[0]));self.assertLessEqual(abs((angle+45)%90-45),3)
        with self.assertRaises(ValueError):candidates(robot,xyz,0,45,[90,45,35,55,90],[90,80,35,10,89],89)
        self.assertEqual(candidates(robot,[0,2000,50],0,45,[90]*5,[90]*5),[])

    def test_guided_pickup_is_a_full_checked_plan_not_old_card_replay(self):
        with patch.object(ManipulationRobot,'ik_directed',side_effect=AssertionError('legacy slow search must not run')):
            plan=build_plan(self.cfg,self.arms,self.target,False,reference=self.reference)
        self.assertEqual(plan['reference']['sample_count'],9);self.assertEqual(plan['reference']['version'],self.reference['version'])
        self.assertEqual(plan['steps'][-1]['label'],'抬升方块')
        self.assertFalse(any(s['label']=='释放方块' for s in plan['steps']))
        self.assertNotEqual(plan['reference']['stages'][1]['angles'],self.reference['samples'][0]['grasp'])
        self.assertEqual(len(plan['poses']),len(plan['steps'])+1)

    def test_outside_nine_point_polygon_still_gets_geometry_candidates(self):
        target=target_at(self.cfg,[0,240],0)
        plan=build_plan(self.cfg,self.arms,target,False,reference=self.reference)
        self.assertTrue(all(s['source']['mode']=='nearest' for s in plan['reference']['stages']))

    def test_full_place_checks_carried_block_and_retracts(self):
        self.cfg['destinations']={'drop':[-260*np.sin(np.radians(15)),260*np.cos(np.radians(15))]}
        reference=build_reference(self.cfg,self.arms,self.teaching)
        self.target.update(task_mode='pick_place',destination=dict(kind='fixed',name='drop'))
        plan=build_plan(self.cfg,self.arms,self.target,False,reference=reference)
        self.assertTrue(any(s['label']=='释放方块' for s in plan['steps']))
        self.assertEqual(plan['steps'][-1]['label'],'返回观察姿态')
        self.assertLess(np.linalg.norm(np.asarray(plan['block_release_pose'])[:3,2]-[0,0,1]),np.sin(np.radians(3)))

    def test_blocked_scene_cannot_be_bypassed_by_teaching(self):
        self.cfg['obstacles']=[dict(center_mm=[0,240,100],half_size_mm=[200,150,150])]
        reference=build_reference(self.cfg,self.arms,self.teaching)
        with self.assertRaises(ValueError):build_plan(self.cfg,self.arms,self.target,False,reference=reference)

    def test_eight_mm_budget_fits_real_opening_but_does_not_fabricate_width(self):
        from manipulation_geometry import parameters
        self.cfg['max_position_error_mm']=8.
        parameters(self.cfg,self.arms)
        self.assertEqual(self.cfg['jaw_open_width_mm'],60)
        self.cfg['jaw_open_width_mm']=40
        with self.assertRaisesRegex(ValueError,'开口余量'):parameters(self.cfg,self.arms)

    def test_eight_mm_integer_search_keeps_direction_and_hard_limits(self):
        self.cfg['max_position_error_mm']=8.;robot=ManipulationRobot(self.cfg,self.arms)
        xyz=[-6.646225,247.855993,self.cfg['table_z_mm']+self.cfg['transfer_height_mm']]
        rows=candidates(robot,xyz,5.2083,45,[90,43,43,50,90],[90,80,35,11,89])
        self.assertTrue(rows)
        for row in rows:
            m,_=robot.fk(row['q']);self.assertLessEqual(np.linalg.norm(m[:3,3]*1000-xyz),8)
            self.assertTrue(all(type(q)is int for q in row['q']));self.assertGreaterEqual(row['q'][4],90)
            jaw=m[:3,:3]@robot.jaw_axis;angle=np.degrees(np.arctan2(jaw[1],jaw[0]))
            self.assertLessEqual(abs((angle-5.2083+45)%90-45),3)

    def test_eight_mm_full_place_compensates_grasp_offset_and_checks_block_goal(self):
        self.cfg['max_position_error_mm']=8.
        self.cfg['destinations']={'drop':[-260*np.sin(np.radians(15)),260*np.cos(np.radians(15))]}
        reference=build_reference(self.cfg,self.arms,self.teaching)
        self.target.update(task_mode='pick_place',destination=dict(kind='fixed',name='drop'))
        plan=build_plan(self.cfg,self.arms,self.target,False,reference=reference)
        self.assertLessEqual(plan['placement_error_mm'],8.)
        self.assertEqual(plan['reference']['position_tolerance_mm'],8.)
        self.assertTrue(all(s['position_error_mm']<=8 for s in plan['reference']['stages']))

    def test_batched_sat_matches_scalar_contact_and_random_axes(self):
        from manipulation_collision import overlaps
        import cad_geometry as cad
        from auto_geometry import rotation
        rng=np.random.default_rng(831);boxes=[]
        for _ in range(300):
            pair=[]
            for j in range(2):
                axis=rng.normal(size=3);axis/=np.linalg.norm(axis)
                pair.append((rng.uniform(-50,50,3),rotation(axis,rng.uniform(-np.pi,np.pi)),rng.uniform(.1,40,3)))
            boxes.append((*pair,rng.uniform(0,8)))
        for separation in (2,2-1e-8,2+1e-8):boxes.append(((np.zeros(3),np.eye(3),np.ones(3)),(np.array([separation,0,0]),np.eye(3),np.ones(3)),0.))
        hit,_=overlaps(*[np.asarray([b[side][k] for b in boxes]) for side in (0,1) for k in (0,1,2)],np.array([b[2] for b in boxes]))
        self.assertEqual(hit.tolist(),[cad.obb_overlap(a,b,m) for a,b,m in boxes])

    def test_batched_collision_matches_scalar_with_carry_and_obstacles(self):
        from manipulation_collision import check_samples
        import cad_geometry as cad
        rng=np.random.default_rng(832);qs=[self.cfg['observation_angles']]
        qs += [[int(rng.integers(lo,hi+1)) for lo,hi in (self.arms['joint_limits'][str(i)] for i in range(1,6))] for _ in range(160)]
        carried=np.eye(4);carried[2,3]=-.0075
        for obstacle in (False,True):
            cfg=copy.deepcopy(self.cfg)
            if obstacle:cfg['obstacles']=[dict(center_mm=[40,260,80],half_size_mm=[15,15,30])]
            robot=ManipulationRobot(cfg,self.arms)
            for attachment in (None,carried):
                for margin in (0,robot._cad_reach*np.radians(1.25)):
                    # One sample per comparison pins both verdict and exact error.
                    for q in qs:
                        errors=[]
                        for fn in (lambda:cad.collision(robot,q,attachment,margin),lambda:check_samples(robot,[q],attachment,margin)):
                            try:fn();errors.append(None)
                            except ValueError as exc:errors.append(str(exc))
                        self.assertEqual(errors[0],errors[1],(q,obstacle,attachment,margin))
                    for start in range(0,len(qs),16):
                        rows=qs[start:start+16];errors=[]
                        for fn in (lambda:[cad.collision(robot,q,attachment,margin) for q in rows],lambda:check_samples(robot,rows,attachment,margin)):
                            try:fn();errors.append(None)
                            except ValueError as exc:errors.append(str(exc))
                        self.assertEqual(errors[0],errors[1])

    def test_batched_and_scalar_sweeps_generate_identical_complete_plan(self):
        from manipulation_guided import GuidedSearch
        fast=build_plan(self.cfg,self.arms,self.target,False,reference=self.reference)
        original=GuidedSearch.__init__
        def scalar(search,*a,**k):original(search,*a,**k);search.robot._batch_sweeps=False
        with patch.object(GuidedSearch,'__init__',scalar):slow=build_plan(self.cfg,self.arms,self.target,False,reference=self.reference)
        self.assertEqual(fast,slow)

    def test_invalid_batched_samples_fail_closed(self):
        from manipulation_collision import check_samples
        robot=ManipulationRobot(self.cfg,self.arms)
        with self.assertRaises(ValueError):check_samples(robot,[[90,80,float('nan'),10,90]])


class GuidedTaskTests(unittest.TestCase):
    setUp=cad_tests.CadTaskTests.setUp
    tearDown=cad_tests.CadTaskTests.tearDown
    wait_done=cad_tests.CadTaskTests.wait_done
    adopt=cad_tests.CadTaskTests.adopt

    def prepare(self):
        self.adopt();self.teaching=teaching_fixture(self.task.load())
        self.demo.teaching=SimpleNamespace(load=lambda:copy.deepcopy(self.teaching),bound=lambda cfg:None)
        target=target_at(self.task.load(),[0,260],0);target['frame_id']=self.meta['id'];return target

    def test_preview_and_execution_validation_bind_current_reference_without_moving(self):
        target=self.prepare();self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        pending=self.c.pending;self.assertEqual(pending['parameters']['plan']['reference']['sample_count'],9)
        self.task.validate_execution(pending);self.assertEqual(StatefulFake.calls,[])
        self.teaching['reported_mapping']['samples'][0]['grasp'][0]+=1
        with self.assertRaisesRegex(ValueError,'九点参考'):self.task.validate_execution(pending)
        self.assertEqual(StatefulFake.calls,[])

    def test_source_change_during_compute_discards_plan(self):
        target=self.prepare();original=self.task.compute_plan
        def changed(*args):
            plan=original(*args);self.teaching['reported_mapping']['samples'][0]['grasp'][0]+=1;return plan
        with patch.object(self.task,'compute_plan',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'九点参考已改变'):self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])

    def test_fake_execution_uses_full_plan_and_does_not_resend(self):
        target=self.prepare();preview=self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        self.c.execute(dict(token=preview['token'],confirmed=True));self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        count=len(StatefulFake.calls);self.assertEqual(count,len(preview['parameters']['plan']['steps']))
        self.c.execute(dict(token=preview['token'],confirmed=True));self.assertEqual(len(StatefulFake.calls),count)

    def test_local_and_ros_compute_overlap_and_disagree_without_motion(self):
        import threading
        target=self.prepare();original=self.task.compute_plan
        barrier=threading.Barrier(2);captured=[]
        def local(*args):barrier.wait(timeout=5);return original(*args)
        def remote(request):
            barrier.wait(timeout=5);captured.append(request)
            plan=build_plan(request['setup'],request['arms'],request['target'],request['require_path'],reference=request['reference'])
            plan['duration_ms']+=1
            return plan
        self.task.planner=SimpleNamespace(request=remote)
        with patch.object(self.task,'compute_plan',side_effect=local):
            with self.assertRaisesRegex(ValueError,'ROS2与本地运动参数不一致'):
                self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        self.assertEqual(len(captured),1);self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])

    def test_bad_existing_nine_point_data_is_not_silently_ignored(self):
        target=self.prepare();self.demo.teaching.bound=lambda cfg:(_ for _ in ()).throw(ValueError('九点来源已改变'))
        self.assertFalse(self.task.status()['nine_point_reference']['available'])
        with self.assertRaisesRegex(ValueError,'来源已改变'):self.task.preview(dict(target=target,scene_confirmed=True))
        self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])


if __name__=='__main__':unittest.main()
