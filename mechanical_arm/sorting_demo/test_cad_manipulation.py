"""无纸模式：全部合成相机/假控制器，不访问真实硬件或网络。"""
import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import cad_geometry as cad
from manipulation_geometry import ManipulationRobot,build_plan,camera_pose,locate,readiness,calibration_version
from test_manipulation import fixture,StatefulFake
import test_manipulation as legacy


def case():
    cfg,arms,_=fixture();cfg['table_z_mm']=-16
    q=[90,80,35,10,90]
    meta=dict(id='a'*32,real=False,image_size=[640,480],
              joints=[dict(id=i,angle=v,read_ok=True,error=None) for i,v in enumerate(q+[10],1)])
    cfg=cad.prepare(cfg,arms,meta,q,dict(kind='fov_estimate',fov_deg=110,axis='diagonal'))
    cfg['cad']['assumptions_confirmed']=True
    target=target_at(cfg,[0,260],0)
    return cfg,arms,meta,target


def target_at(cfg,xy,yaw):
    angle=np.radians(yaw);R=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    edge=np.array([[-15,-15],[15,-15]])@R.T+xy
    world=np.c_[np.vstack([xy,edge]),np.full(3,cfg['table_z_mm']+30)]
    k,d,r,t=camera_pose(cfg)
    px=cv2.projectPoints(world,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
    lo=px.min(axis=0)-10;hi=px.max(axis=0)+10
    return dict(source='manual',task_mode='pickup',frame_id='a'*32,image_size=[640,480],block_size_id='block',
                bbox=[*lo,*hi],center_px=px[0].tolist(),edge_px=px[1:].tolist())


class CadGeometryTests(unittest.TestCase):
    def test_depth_first_order_matches_breadth_first_and_checks_fewer_edges(self):
        from auto_geometry import Robot
        cfg,arms,_,_=case();robot=ManipulationRobot(cfg,arms)
        start=np.asarray(cfg['observation_angles'],float);end=(start+1).astype(int).tolist()
        rng=np.random.default_rng(701)
        all_edges=[(mask,j) for mask in range(32) for j in range(5) if not mask&(1<<j)]
        for sample in range(61):
            blocked=set() if sample==0 else set(all_edges) if sample==1 else {edge for edge in all_edges if rng.random()<.5}
            results=[];counts=[]
            def check(q,carried=None,margin=0):
                delta=np.asarray(q)-start
                moving=np.flatnonzero((delta>0)&(delta<1))
                if len(moving):
                    mask=sum(1<<i for i,v in enumerate(delta) if v==1)
                    if (mask,int(moving[0])) in blocked:raise ValueError('blocked edge')
                return np.eye(4)
            for dfs in (False,True):
                with patch.object(robot,'collision',side_effect=check) as collision:
                    try:results.append(Robot.transition(robot,start.tolist(),end,depth_first=dfs))
                    except ValueError:results.append(None)
                    counts.append(collision.call_count)
            self.assertEqual(results[0],results[1],f'different route on graph {sample}')
            if sample==0:self.assertLess(counts[1],counts[0]/3)

    def test_colliding_endpoint_skips_all_route_and_waypoint_searches(self):
        from auto_geometry import Robot
        from manipulation_geometry import PathEndpointError
        cfg,arms,_,_=case();robot=ManipulationRobot(cfg,arms)
        start=cfg['observation_angles'];end=start.copy();end[0]+=1
        def check(q,carried=None,margin=0):
            if list(q)==end:raise ValueError('CAD连杆、相机或夹爪包络接近桌面')
            return np.eye(4)
        with patch.object(robot,'collision',side_effect=check) as checks,patch.object(Robot,'transition',side_effect=AssertionError('must not search')):
            with self.assertRaisesRegex(PathEndpointError,'目标姿态.*接近桌面'):robot.transition(start,end)
            self.assertEqual(checks.call_count,2)

    def test_endpoint_failure_does_not_try_neutral_pose(self):
        from manipulation_geometry import PathEndpointError,_build_plan
        cfg,arms,_,target=case()
        with patch.object(ManipulationRobot,'grouped_transition',side_effect=PathEndpointError('目标姿态不满足碰撞包络')) as search:
            with self.assertRaisesRegex(ValueError,'到取物上方.*目标姿态'):_build_plan(cfg,arms,target,False,45)
            self.assertEqual(search.call_count,1)

    def test_vectorized_boxes_preserve_collision_verdicts(self):
        from test_data.collision_reference import collision as reference
        cfg,arms,_,_=case();cfg['table_z_mm']=0
        robot=ManipulationRobot(cfg,arms);rng=np.random.default_rng(61)
        for _ in range(120):
            q=[int(rng.integers(*[v,v2+1])) for v,v2 in (arms['joint_limits'][str(i)] for i in range(1,6))]
            results=[]
            for fn in (reference,cad.collision):
                try:results.append(('ok',fn(robot,q,None,7)))
                except ValueError as exc:results.append(('error',str(exc)))
            self.assertEqual(results[0][0],results[1][0])
            if results[0][0]=='error':self.assertEqual(results[0][1],results[1][1])
            else:np.testing.assert_allclose(results[0][1],results[1][1],atol=1e-12)

    def test_vectorized_sat_matches_prior_axes_and_contact_rule(self):
        from auto_geometry import rotation
        def reference(a,b,margin):
            ca,ra,ha=a;cb,rb,hb=b;delta=cb-ca
            if np.any(abs(delta)>abs(ra)@ha+abs(rb)@hb+2*margin):return False
            for axis in [*ra.T,*rb.T,*(np.cross(x,y) for x in ra.T for y in rb.T)]:
                length=np.linalg.norm(axis)
                if length<1e-9:continue
                axis=axis/length
                radius=np.sum(abs(ra.T@axis)*ha)+np.sum(abs(rb.T@axis)*hb)+2*margin
                if abs(float(delta@axis))>radius:return False
            return True
        rng=np.random.default_rng(51)
        for _ in range(500):
            boxes=[]
            for i in range(2):
                axis=rng.normal(size=3);axis/=np.linalg.norm(axis)
                boxes.append((rng.uniform(-50,50,3),rotation(axis,rng.uniform(-np.pi,np.pi)),rng.uniform(.1,40,3)))
            margin=rng.uniform(0,8)
            self.assertEqual(cad.obb_overlap(*boxes,margin),reference(*boxes,margin))
        a=(np.zeros(3),np.eye(3),np.ones(3))
        for delta,expected in ((2,True),(2-1e-8,True),(2+1e-8,False)):
            b=(np.array([delta,0,0]),np.eye(3),np.ones(3))
            self.assertEqual(cad.obb_overlap(a,b),expected)

    def test_projection_uses_frozen_angles_without_replacing_setup(self):
        cfg,_,_,target=case();angles=cfg['observation_readings'].copy();angles[1]+=1
        k,d,r,t=camera_pose(cfg,angles)
        w=np.array([[0,260,14],[-15,245,14],[15,245,14]],float)
        p=cv2.projectPoints(w,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
        target.update(center_px=p[0].tolist(),edge_px=p[1:].tolist(),
                      bbox=[*p.min(axis=0)-10,*p.max(axis=0)+10],_frame_joint_angles=angles)
        before=copy.deepcopy(cfg)
        np.testing.assert_allclose(locate(cfg,target)['xyz_mm'],[0,260,14],atol=1e-6)
        self.assertEqual(cfg,before)
    def test_browser_number_serialization_does_not_change_version(self):
        cfg,_,_,_=case();browser=copy.deepcopy(cfg)
        browser['max_position_error_mm']=int(browser['max_position_error_mm'])
        browser['model']['tcp_rpy_deg']=[0.,0.,0.]
        self.assertEqual(calibration_version(cfg),calibration_version(browser))
    def test_camera_belongs_to_wrist_link4_and_uses_real_angles(self):
        cfg,_,_,_=case();q=cfg['observation_readings'].copy()
        before=cad.camera_extrinsics(cfg,q)
        q[4]+=20;self.assertEqual(before,cad.camera_extrinsics(cfg,q))
        q[3]+=5;self.assertNotEqual(before,cad.camera_extrinsics(cfg,q))
        cfg['cad']['camera_mount']['rotation_optical_to_link']=[[0]*3]*3
        with self.assertRaises(ValueError):cad.camera_extrinsics(cfg,q)

    def test_fov_is_labelled_estimate_and_import_requires_exact_resolution(self):
        a,_,s=cad.intrinsics([640,480],dict(kind='fov_estimate',axis='diagonal',fov_deg=110))
        b,_,_=cad.intrinsics([640,480],dict(kind='fov_estimate',axis='horizontal',fov_deg=110))
        self.assertAlmostEqual(a[0][0]/b[0][0],1.25)
        self.assertFalse(s['accuracy_verified'])
        data=dict(kind='imported',image_size=[640,480],matrix=a,distortion=[0]*5,source='SYNTHETIC camera')
        cad.intrinsics([640,480],data)
        with self.assertRaises(ValueError):cad.intrinsics([1280,720],data)
        data['matrix'][0][0]=float('nan')
        with self.assertRaises(ValueError):cad.intrinsics([640,480],data)

    def test_candidates_do_not_forge_measurements_or_original_gripper(self):
        cfg,arms,_,target=case()
        self.assertFalse(cfg['model_reviewed']);self.assertFalse(cfg['calibration_reviewed'])
        self.assertEqual(cfg['model']['measurements'],[]);self.assertEqual(cfg['camera']['samples'],[])
        self.assertEqual(cfg['cad']['gripper']['open'],10);self.assertEqual(arms['gripper']['open'],80)
        self.assertEqual(cfg['cad']['gripper']['closed'],arms['gripper']['closed'])
        self.assertTrue(readiness(cfg,arms)['ready']);self.assertFalse(readiness(cfg,arms,True)['ready'])
        cfg['cad']['assumptions_confirmed']=False
        self.assertFalse(readiness(cfg,arms)['ready'])

    def test_no_paper_manual_and_vision_locate_identically(self):
        cfg,_,_,target=case();cfg['board']={};result=locate(cfg,target)
        np.testing.assert_allclose(result['xyz_mm'],[0,260,14],atol=1e-6)
        self.assertEqual(result['destination_kind'],'none')
        target['source']='vision';self.assertEqual(result,locate(cfg,target))
        cfg['cad']['intrinsics_recipe']['axis']='horizontal'
        with self.assertRaises(ValueError):cad.validate(cfg)

    def test_bounds_and_scene_obstacle_cannot_be_hidden(self):
        cfg,arms,_,_=case();r=ManipulationRobot(cfg,arms)
        r.collision(cfg['observation_angles'])
        cfg['cad']['collision_boxes'][4][0]['half_size_mm']=[.001]*3
        with self.assertRaises(ValueError):ManipulationRobot(cfg,arms)
        cfg,arms,_,_=case();cfg['obstacles']=[dict(center_mm=[0,150,100],half_size_mm=[100,100,100])]
        with self.assertRaises(ValueError):ManipulationRobot(cfg,arms).collision(cfg['observation_angles'])

    def test_profile_covers_whole_triangles_including_camera(self):
        p=cad.profile();dtype=np.dtype([('n','<f4',(3,)),('v','<f4',(3,3)),('s','<u2')])
        for index,name in enumerate(('base_link','link1','link2','link3','link4','link5')):
            data=(cad.ASSETS/'meshes'/(name+'.STL')).read_bytes()
            faces=np.frombuffer(data,dtype=dtype,offset=84)['v'].astype(float)*1000
            lo=faces.min(axis=1);hi=faces.max(axis=1);covered=np.zeros(len(faces),bool)
            for box in p['collision_boxes'][index]:
                c=np.asarray(box['center_mm']);h=np.asarray(box['half_size_mm'])
                covered|=np.all(lo>=c-h-1e-6,axis=1)&np.all(hi<=c+h+1e-6,axis=1)
            self.assertTrue(covered.all(),name+' has uncovered triangles')

    def test_pickup_plan_uses_tilt_and_never_releases_or_returns(self):
        cfg,arms,_,target=case();start=time.monotonic()
        plan=build_plan(cfg,arms,target,False)
        self.assertEqual(plan['geometry_source'],'cad_estimate');self.assertFalse(plan['accuracy_verified'])
        self.assertGreater(plan['approach_tilt_deg'],0)
        self.assertEqual(plan['steps'][-1]['label'],'抬升方块')
        self.assertFalse(any(s['label'] in ('释放方块','返回观察姿态') for s in plan['steps']))
        self.assertEqual(plan['steps'][0]['label'],'张开夹爪并到取物上方')
        self.assertEqual(plan['steps'][0]['joints'][-1],[6,10])
        self.assertLessEqual(len(plan['steps']),200)
        artifact=Path(__file__).parent.parent/'implementation_backups/paperless_20261016/synthetic_cad_case.json'
        artifact.write_text(json.dumps(dict(setup=cfg,arms=arms,target=target,plan=plan),ensure_ascii=False,allow_nan=False))
        print('CAD synthetic pickup:',len(plan['steps']),'commands,',round(time.monotonic()-start,2),'s',flush=True)

    def test_full_plan_releases_and_preserves_flat_block(self):
        cfg,arms,_,target=case();cfg['destinations']={'cad_drop':[-260*np.sin(np.radians(15)),260*np.cos(np.radians(15))]}
        target.update(task_mode='pick_place',destination=dict(kind='fixed',name='cad_drop'))
        plan=build_plan(cfg,arms,target,False)
        self.assertEqual(plan['steps'][-1]['label'],'返回观察姿态')
        self.assertTrue(any(s['label']=='释放方块' for s in plan['steps']))
        self.assertLess(np.linalg.norm(np.asarray(plan['block_release_pose'])[:3,2]-[0,0,1]),.053)


class CadTaskTests(unittest.TestCase):
    def setUp(self):
        legacy.TaskTests.setUp(self)
        self.c.real=False
        cfg,_,_,_=case()
        from manipulation_geometry import defaults
        initial=defaults();initial.update(block_size_mm=[30,30,30],table_z_mm=-16)
        self.task.path.write_text(json.dumps(initial))
        StatefulFake.values=cfg['observation_angles']+[10]
        from preview_manipulation_simulation import SyntheticCamera
        self.demo.camera=SyntheticCamera()
        self.meta=self.task.capture({})
        self.task.prepare_cad(dict(frame_id=self.meta['id'],observation_angles=cfg['observation_angles'],
            intrinsics_recipe=cfg['cad']['intrinsics_recipe'],image_roll_deg=0,expected_sha256=self.task.sha()))

    def tearDown(self):
        self.wait_done();self.tmp.cleanup()

    def wait_done(self):
        deadline=time.monotonic()+30
        while self.c.busy and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.c.busy,'fake worker timeout')

    def adopt(self):
        cfg=self.task.load();cfg['cad']['assumptions_confirmed']=True
        self.task.save(dict(setup=cfg,expected_sha256=self.task.sha()))

    def test_planning_does_not_hold_control_lock_and_rejects_changed_inputs(self):
        import threading
        self.adopt();cfg=self.task.load();target=target_at(cfg,[0,260],0);target['frame_id']=self.meta['id']
        entered=threading.Event();resume=threading.Event();errors=[]
        original=self.task.compute_plan
        def paused(*args):
            entered.set()
            if not resume.wait(5):raise AssertionError('test failed to release planning')
            return original(*args)
        def run():
            try:self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
            except Exception as exc:errors.append(exc)
        with patch.object(self.task,'compute_plan',side_effect=paused):
            worker=threading.Thread(target=run);worker.start()
            self.assertTrue(entered.wait(5))
            try:
                acquired=self.c.lock.acquire(timeout=.2)
                self.assertTrue(acquired,'CPU planning must not block control/status')
                if acquired:
                    self.assertFalse(self.c.status()['busy']);self.c.lock.release()
                self.assertEqual(self.task.status()['planning']['status'],'running')
                with self.assertRaisesRegex(ValueError,'已有计划'):self.task.preview(dict(target=target))
                cfg['scene_revision']='changed during computation';self.task.path.write_text(json.dumps(cfg))
            finally:resume.set();worker.join(20)
        self.assertFalse(worker.is_alive());self.assertEqual(len(errors),1)
        self.assertRegex(str(errors[0]),'配置或证据变化')
        self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])

    def test_pure_worker_timeout_never_creates_motion_token(self):
        self.adopt();cfg=self.task.load();target=target_at(cfg,[0,260],0);target['frame_id']=self.meta['id']
        with patch.object(self.task,'PLAN_COMPUTE_SECONDS',.001):
            with self.assertRaisesRegex(ValueError,'计划计算超时'):
                self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])
        self.assertEqual(self.task.status()['planning']['status'],'failed')

    def test_invalidating_during_computation_discards_the_result(self):
        self.adopt();cfg=self.task.load();target=target_at(cfg,[0,260],0);target['frame_id']=self.meta['id']
        original=self.task.compute_plan
        def revoked(*args):
            self.task.invalidate()
            return original(*args)
        with patch.object(self.task,'compute_plan',side_effect=revoked):
            with self.assertRaisesRegex(ValueError,'预览已撤销'):
                self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        self.assertIsNone(self.c.pending);self.assertEqual(StatefulFake.calls,[])

    def test_setup_preview_execute_and_dedup_without_paper_or_detection(self):
        self.assertFalse(self.task.status()['readiness']['ready']);self.assertEqual(StatefulFake.calls,[])
        self.adopt();cfg=self.task.load();target=target_at(cfg,[0,260],0);target['frame_id']=self.meta['id']
        target['_frame_joint_angles']=[0]*5  # 外部输入不得决定实际投影关节读数。
        with patch('auto_task.detect',side_effect=AssertionError('vision prohibited')):
            p=self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
            self.assertEqual(p['parameters']['target']['_frame_joint_angles'],cfg['observation_readings'])
            self.assertEqual(StatefulFake.calls,[])
            with patch.object(self.task,'capture',side_effect=AssertionError('camera reread prohibited')):
                self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        calls=len(StatefulFake.calls);self.assertEqual(calls,len(p['parameters']['plan']['steps']))
        self.c.execute(dict(token=p['token'],confirmed=True));self.assertEqual(len(StatefulFake.calls),calls)
        self.task.result(dict(task_id=p['token'],result='success',note='SYNTHETIC ONLY'))
        self.assertTrue(all(v['trials']==0 for v in self.task.acceptance().values()))
        print('CAD fake full flow:',calls,'writes, duplicate not resent, no real acceptance',flush=True)

    def test_cannot_mark_cad_as_measured_or_record_simulated_path(self):
        self.adopt();cfg=self.task.load();cfg['model_reviewed']=True
        with self.assertRaises(ValueError):self.task.save(dict(setup=cfg,expected_sha256=self.task.sha()))
        with self.assertRaises(ValueError):self.task.validation(dict(draft_id='missing',confirmed=True))

    def test_edited_source_invalidates_pending_plan_before_any_write(self):
        self.adopt();cfg=self.task.load();target=target_at(cfg,[0,260],0);target['frame_id']=self.meta['id']
        p=self.task.preview(dict(target=target,scene_confirmed=True,dry_run=False))
        cfg=self.task.load();cfg['cad']['camera_mount']['xyz_mm'][0]+=1
        self.task.path.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):self.c.execute(dict(token=p['token'],confirmed=True))
        self.assertEqual(StatefulFake.calls,[])


if __name__=='__main__':unittest.main()
