"""全合成标定、静态假相机与有状态假舵机；绝不导入Arm_Lib或连接设备。"""
from contextlib import nullcontext
import copy
import json
import io
from pathlib import Path
import tempfile
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from core import EventStore
from control import Control
from test_auto import fixture as old_fixture
from manipulation_geometry import (defaults,ManipulationRobot,parameters,project_to_plane,
    fit_extrinsics,locate,model_report,calibration_report,readiness,calibration_version,build_plan,robot_description)
from manipulation_task import Manipulation
from auto_plan import arm_fingerprint


def fixture():
    old,arms=old_fixture();cfg=defaults()
    for key in cfg:
        if key in old and key not in ('camera','model'):cfg[key]=copy.deepcopy(old[key])
    cfg.update(table_z_mm=-100,workspace_xy_mm=[[-150,20],[150,20],[150,350],[-150,350]],
        motion_bounds_mm=[[-350,-350,-200],[350,450,600]],scene_revision='SYNTHETIC TEST ONLY',
        max_direction_error_deg=3,transfer_height_mm=160,destinations={'test':[60,200]},
        grasp_reference_angles=[90,30,30,30,90],model_reviewed=True,calibration_reviewed=True,
        observation_angles=[90,50,32,82,90],observation_readings=[90,50,32,82,90])
    cfg['camera'].update(matrix=old['camera']['matrix'],distortion=[0]*5,
        base_to_camera=dict(rvec=[np.pi,0,0],tvec_mm=[0,200,500]))
    cfg['model'].update(old['model']);cfg['model']['jaw_axis_tcp']=[1,0,0]
    cfg['model']['base_box_mm']=dict(center_mm=[0,-70,41],half_size_mm=[60,130,42])
    robot=ManipulationRobot(cfg,arms)
    cfg['model']['measurements']=[dict(angles=q,xyz_mm=(robot.fk(q)[0][:3,3]*1000).tolist())
        for q in [[90,50,32,82,90],[95,55,32,82,90],[85,45,40,80,90]]]
    q=[100,50,40,80,90]
    cfg['model']['independent_measurements']=[dict(angles=q,xyz_mm=(robot.fk(q)[0][:3,3]*1000).tolist())]
    cfg['board'].update(points_mm=[[0,0],[100,0],[100,100],[0,100]],origin_in_base_mm=[-50,100,-100],yaw_deg=0)
    def project(xy,z):return [320+800*xy[0]/(500-z),240-800*(xy[1]-200)/(500-z)]
    cfg['checks']=[dict(kind=kind,xy_mm=xy,pixel=project(xy,-70 if kind=='top' else -100))
        for kind in ('table','top') for xy in [[-60,140],[60,140],[10,270]]]
    cfg['arm_config_sha256']=arm_fingerprint(arms)
    xy=[0,145.65];edge=[project([-15,xy[1]-15],-70),project([15,xy[1]-15],-70)]
    center=project(xy,-70)
    target=dict(source='manual',frame_id='a'*32,image_size=[640,480],block_size_id='block',
        center_px=center,edge_px=edge,bbox=[center[0]-24,center[1]-24,center[0]+24,center[1]+24],destination=dict(kind='fixed',name='test'))
    return cfg,arms,target


class GeometryTests(unittest.TestCase):
    def test_defaults_fail_closed(self):
        cfg,arms,_=fixture();self.assertFalse(readiness(defaults(),arms)['ready'])
        self.assertTrue(readiness(cfg,arms)['ready'])
        json.dumps(readiness(cfg,arms),allow_nan=False)
        self.assertFalse(readiness(cfg,arms,True)['ready'])

    def test_projection_height_distortion_and_removed_paper(self):
        cfg,_,target=fixture();p=project_to_plane(cfg,target['center_px'],-70)
        np.testing.assert_allclose(p,[0,145.65,-70],atol=1e-6)
        self.assertGreater(np.linalg.norm(project_to_plane(cfg,target['center_px'],-100)[:2]-p[:2]),2)
        cfg['board']['anchor_pixels']=[]
        np.testing.assert_allclose(project_to_plane(cfg,target['center_px'],-70),p)
        cfg['camera']['distortion']=[-.1,.02,.001,.001,0]
        pixels=cv2.projectPoints(np.array([[20.,160.,-70.]]),np.array([np.pi,0,0]),np.array([0.,200,500]),np.array(cfg['camera']['matrix'],float),np.array(cfg['camera']['distortion']))[0].reshape(2)
        np.testing.assert_allclose(project_to_plane(cfg,pixels.tolist(),-70),[20,160,-70],atol=.001)

    def test_board_correspondence_and_degeneracy(self):
        cfg,_,_=fixture();world=np.array([[-50,100,-100],[50,100,-100],[50,200,-100],[-50,200,-100]],float)
        pixels=cv2.projectPoints(world,np.array([np.pi,0,0]),np.array([0.,200,500]),np.array(cfg['camera']['matrix'],float),np.zeros(5))[0].reshape(-1,2)
        result=fit_extrinsics(cfg,pixels.tolist());self.assertLess(result['max_reprojection_px'],1e-4)
        cfg['board']['points_mm']=[[0,0]]*4
        with self.assertRaises(ValueError):fit_extrinsics(cfg,pixels.tolist())

    def test_manual_and_vision_are_same_geometry(self):
        cfg,_,target=fixture();manual=locate(cfg,target);target['source']='vision'
        self.assertEqual(manual,locate(cfg,target))
        target['destination']={'kind':'pixel','pixel':[400,240]}
        result=locate(cfg,target);np.testing.assert_allclose(result['destination_xy_mm'],[60,200])

    def test_invalid_selections(self):
        cfg,_,target=fixture()
        for key,value in [('center_px',[-1,0]),('center_px',[10,10]),('edge_px',[target['center_px']]*2),
            ('image_size',[1280,720]),('block_size_id','another'),('bbox',[1,1,1,1]),('bbox',[True,1,20,20])]:
            bad=copy.deepcopy(target);bad[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):locate(cfg,bad)
        for destination in [dict(kind='fixed',name='missing'),dict(kind='pixel',pixel=target['center_px'])]:
            bad=copy.deepcopy(target);bad['destination']=destination
            with self.assertRaises(ValueError):locate(cfg,bad)

    def test_independent_checks_and_model_evidence(self):
        cfg,arms,_=fixture();calibration_report(cfg);model_report(cfg,arms)
        cfg['checks'][0]['pixel'][0]+=20
        with self.assertRaises(ValueError):calibration_report(cfg)
        cfg,arms,_=fixture();cfg['model']['independent_measurements']=cfg['model']['measurements'][:1]
        with self.assertRaises(ValueError):model_report(cfg,arms)

    def test_directed_ik_rotation_and_unreachable(self):
        cfg,arms,_=fixture();robot=ManipulationRobot(cfg,arms)
        for yaw in (0,30,75):
            q=robot.ik_directed([0,145.65,-85],[90,32,29,29,90],yaw)
            m,_=robot.fk(q);axis=(m[:3,:3]@robot.jaw_axis)[:2]
            actual=np.degrees(np.arctan2(axis[1],axis[0]));error=abs((actual-yaw+45)%90-45)
            self.assertLessEqual(error,3);self.assertTrue(all(type(x)is int for x in q))
            self.assertLess(np.linalg.norm(m[:3,3]*1000-[0,145.65,-85]),1)
        with self.assertRaises(ValueError):robot.ik_directed([1000,1000,1000],[90,30,30,30,90],0)

    def test_complete_plan_and_separate_motion_space(self):
        cfg,arms,target=fixture();cfg['transfer_height_mm']=50;cfg['destinations']['test']=[70,127.7]
        plan=build_plan(cfg,arms,target,False)
        self.assertEqual(len(plan['poses']),len(plan['steps'])+1)
        self.assertEqual(plan['steps'][0]['joints'],[[6,80]])
        self.assertTrue(any(s['label']=='释放方块' for s in plan['steps']))
        self.assertEqual(plan['steps'][-1]['label'],'返回观察姿态')
        self.assertNotEqual(cfg['workspace_xy_mm'],cfg['motion_bounds_mm'])
        cfg['model']['tool_boxes_mm'][0]['half_size']=[1000,1000,1000]
        with self.assertRaises(ValueError):build_plan(cfg,arms,target,False)

    def test_obstacles_carried_object_and_limits(self):
        cfg,arms,_=fixture();r=ManipulationRobot(cfg,arms);q=[90,50,32,82,90];m,_=r.fk(q)
        cfg['obstacles']=[dict(center_mm=(m[:3,3]*1000).tolist(),half_size_mm=[20,20,20])]
        r=ManipulationRobot(cfg,arms)
        with self.assertRaises(ValueError):r.collision(q,np.eye(4))
        cfg,arms,_=fixture();arms['joint_limits']['5']=[89,250]
        with self.assertRaises(ValueError):parameters(cfg,arms)

    def test_base_collision_and_exported_tcp_frame(self):
        cfg,arms,_=fixture();xml=ET.fromstring(robot_description(cfg,arms))
        self.assertEqual(xml.find("joint[@name='measured_tcp']/child").get('link'),'tcp')
        self.assertEqual(xml.find("joint[@name='measured_tcp']/origin").get('xyz'),'0.0 0.0 0.12')
        self.assertIsNotNone(xml.find("link[@name='base_link']/collision/geometry/box"))
        cfg['model']['base_box_mm']=dict(center_mm=[0,150,100],half_size_mm=[1000,1000,1000])
        with self.assertRaises(ValueError):ManipulationRobot(cfg,arms).collision([90,50,32,82,90])


class StatefulFake:
    values=[];calls=[];fail_write=None;fail_read=None;reads=0;failed_joints={};blocked_gripper=None;shortfall={};simulated_retries={};retries_requested=None
    def __init__(self,cfg,real=False):self.config,self.real=cfg,real
    def read_report(self):
        type(self).reads+=1
        if self.fail_read==self.reads:raise RuntimeError('synthetic read failure')
        failed=self.failed_joints.get(self.reads,())
        return [dict(id=i,angle=None,read_ok=False,error='synthetic I2C error') if i in failed else
                dict(id=i,angle=v,read_ok=True,error=None) for i,v in enumerate(self.values,1)]
    holds=[]
    def write(self,pairs,hold_ms=None,retries=0):
        from core import arm
        if self.real and not self.config['hardware_ready']:raise ValueError('unready')
        type(self).holds.append(hold_ms);type(self).retries_requested=retries
        # 模拟控制层已重发并成功的写入记录。
        self.last_retries=list(self.simulated_retries.get(len(self.calls)+1,[]))
        pairs=[(i,arm.angle(self.config,i,v)) for i,v in pairs];arm.timing(self.config)
        self.calls.append(pairs)
        if self.fail_write==len(self.calls):raise RuntimeError('synthetic write failure')
        for i,v in pairs:self.values[i-1]=v
        # 模拟方块阻挡夹爪：闭合目标之前停住。
        if self.blocked_gripper is not None and any(i==6 and v>self.blocked_gripper for i,v in pairs):self.values[5]=self.blocked_gripper
        # 模拟负载下关节未完全到位。
        for i,v in pairs:
            if i in self.shortfall:self.values[i-1]=v-self.shortfall[i]


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name in ['AGENTS.md','机械臂操作准则.md','arm_playground/pose_setup.py','arm_playground/joint_check.py','camera_preview/server.py']:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fake audit fixture')
        self.cfg,self.arms,self.target=fixture();self.arm_path=self.root/'arm_playground/config.json';self.arm_path.write_text(json.dumps(self.arms))
        self.demo_path=self.root/'sorting_demo/config.json';self.demo_path.parent.mkdir();self.demo_path.write_text('{}')
        StatefulFake.values=self.cfg['observation_angles']+[80];StatefulFake.calls=[];StatefulFake.fail_write=None;StatefulFake.fail_read=None;StatefulFake.reads=0;StatefulFake.failed_joints={};StatefulFake.blocked_gripper=None;StatefulFake.shortfall={};StatefulFake.holds=[];StatefulFake.simulated_retries={}
        self.c=Control(self.arm_path,real=True,factory=StatefulFake,root=self.root,lock_factory=nullcontext)
        self.demo=SimpleNamespace(control=self.c,config_path=self.demo_path,store=EventStore(self.root/'events.jsonl'))
        self.task=Manipulation(self.demo);self.task.path.write_text(json.dumps(self.cfg))
        self.c.extra_audit_paths.append(self.task.path)

    def tearDown(self):self.wait_done();self.tmp.cleanup()
    def wait_done(self):
        for _ in range(2000):
            if not self.c.busy:return
            time.sleep(.002)
        self.fail('worker timeout')

    def pending(self):
        steps=[dict(label='test',joints=[[1,95]],motion_ms=1500,settle_ms=300),dict(label='test2',joints=[[2,55]],motion_ms=1500,settle_ms=300)]
        args=dict(operation='manipulation',plan=dict(steps=steps,target={'destination_kind':'fixed'},calibration_version=calibration_version(self.cfg)),target=self.target)
        rows=self.c.read_rows(self.arms)
        return self.task.pending(args,rows,self.c.audit(args))

    def test_execution_dedup_restart_and_no_camera(self):
        with patch.object(self.task,'validate_execution'),patch.object(self.task,'capture',side_effect=AssertionError('no camera')):
            p=self.pending();self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
            self.assertEqual(self.c.job['status'],'completed');self.assertEqual(len(StatefulFake.calls),2)
            self.c.execute(dict(token=p['token'],confirmed=True));self.assertEqual(len(StatefulFake.calls),2)
            replacement=Control(self.arm_path,real=True,factory=StatefulFake,root=self.root,lock_factory=nullcontext)
            replacement.execute(dict(token=p['token'],confirmed=True));self.assertEqual(len(StatefulFake.calls),2)

    def test_write_and_read_failure_stop_without_retry(self):
        for kind in ('write','read'):
            with self.subTest(kind=kind),patch.object(self.task,'validate_execution'):
                StatefulFake.values=self.cfg['observation_angles']+[80];StatefulFake.calls=[];StatefulFake.reads=0
                StatefulFake.fail_write=1 if kind=='write' else None
                # pending读取1次，_work起始读取1次，首步后读取失败。
                StatefulFake.fail_read=3 if kind=='read' else None
                p=self.pending();self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
                self.assertEqual(self.c.job['status'],'failed');self.assertEqual(len(StatefulFake.calls),1)

    def test_entire_batch_preflight(self):
        with patch.object(self.task,'validate_execution'):
            p=self.pending();self.c.pending['parameters']['plan']['steps'][1]['joints']=[[5,89]]
            self.c.pending['audit']=self.c.audit(self.c.pending['parameters'])
            self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
            self.assertEqual(StatefulFake.calls,[]);self.assertEqual(self.c.job['status'],'failed')

    def test_expiry_configuration_change_unready_and_real_reads(self):
        p=self.pending();self.c.pending['expires']=time.monotonic()-1
        with self.assertRaises(ValueError):self.c.execute(dict(token=p['token'],confirmed=True))
        p=self.pending();self.task.path.write_text(json.dumps(dict(self.cfg,scene_revision='changed')))
        with self.assertRaises(ValueError):self.c.execute(dict(token=p['token'],confirmed=True))
        with patch.object(self.task,'validate_execution'):
            self.arms['hardware_ready']=False;self.arm_path.write_text(json.dumps(self.arms));p=self.pending()
            self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
            self.assertEqual(StatefulFake.calls,[])

    def test_cannot_forge_path_records_or_simulated_verification(self):
        cfg=copy.deepcopy(self.cfg);cfg['path_validations']=[dict(real=True,passed=True)]
        with self.assertRaises(ValueError):self.task.save(dict(setup=cfg,expected_sha256=self.task.sha()))
        self.task.drafts['x']=dict(real=False,failed=False,next_step=1,total=1,version=calibration_version(self.cfg))
        with self.assertRaises(ValueError):self.task.validation(dict(draft_id='x',confirmed=True))

    def test_changes_clear_reviews_and_preserve_backup(self):
        cfg=copy.deepcopy(self.cfg);cfg['release_gap_mm']+=1
        r=self.task.save(dict(setup=cfg,expected_sha256=self.task.sha()))
        self.assertFalse(r['setup']['model_reviewed']);self.assertIsNone(r['setup']['arm_config_sha256'])
        self.assertEqual(len(list(self.task.path.parent.glob('manipulation_setup.before_*.json'))),1)

    def test_result_is_operator_only_and_simulation_not_acceptance(self):
        event=dict(task_id='t',operation='manipulation',status='completed',mode='simulation',destination_kind='fixed',
            started_at='1',plan={'target':{},'calibration_version':calibration_version(self.cfg)})
        self.demo.store.append(event)
        result=self.task.result(dict(task_id='t',result='success'))
        self.assertEqual(result['acceptance']['fixed']['trials'],0)

    def test_http_routes_csrf_and_serializable_status(self):
        from server import demo_handler
        from preview_manipulation_simulation import SyntheticCamera
        self.demo.camera=SyntheticCamera()
        self.demo.manipulation=self.task
        handler_class=demo_handler(self.demo)
        response={}
        def call(path,body=None,origin='http://unit.test',csrf=True):
            h=object.__new__(handler_class);h.path=path
            h.reply=lambda status,mime,data:response.update(status=status,mime=mime,data=data)
            if body is None:h.do_GET()
            else:
                body=copy.deepcopy(body)
                if csrf:body['csrf']=self.c.csrf
                data=json.dumps(body).encode()
                h.headers={'Origin':origin,'Host':'unit.test','Content-Type':'application/json','Content-Length':str(len(data))}
                h.rfile=io.BytesIO(data);h.do_POST()
            return response['status'],response['data']
        status,data=call('/manipulation/status');self.assertEqual(status,200);json.loads(data)
        for path in ('/manipulation','/manipulation_ui.js','/manipulation_view.js'):
            self.assertEqual(call(path)[0],200)
        self.assertEqual(call('/manipulation/capture',{},csrf=False)[0],403)
        self.assertEqual(call('/manipulation/capture',{},origin='http://other.test')[0],403)
        with patch('auto_task.detect',side_effect=AssertionError('must not detect')):
            status,data=call('/manipulation/capture',{});self.assertEqual(status,200)
            meta=json.loads(data);self.assertEqual(meta['image_size'],[640,480])
            self.target['frame_id']=meta['id']
            self.assertEqual(call('/manipulation/target',{'target':self.target})[0],200)
        self.assertEqual(StatefulFake.calls,[])

    def test_entire_empty_verification_requires_order_and_real_receipts(self):
        self.cfg['transfer_height_mm']=50;self.cfg['destinations']['test']=[70,127.7]
        self.task.path.write_text(json.dumps(self.cfg))
        with patch.object(self.task,'check_evidence'),patch.object(self.task,'target',return_value=(locate(self.cfg,self.target),{})):
            draft=self.task.preview(dict(target=self.target,dry_run=True,scene_confirmed=True))
        identifier=draft['draft_id']
        with self.assertRaises(ValueError):self.task.step(dict(draft_id=identifier,index=1,empty_confirmed=True))
        with self.assertRaises(ValueError):self.task.validation(dict(draft_id=identifier,confirmed=True))
        for index in range(len(draft['plan']['steps'])):
            p=self.task.step(dict(draft_id=identifier,index=index,empty_confirmed=True))
            self.c.execute(dict(token=p['token'],confirmed=True));self.wait_done()
            self.assertEqual(self.c.job['status'],'completed')
        result=self.task.validation(dict(draft_id=identifier,confirmed=True))
        self.assertEqual(len(result['setup']['path_validations']),1)
        self.task.check_path_evidence(result['setup'])
        with self.assertRaises(ValueError):self.task.validation(dict(draft_id=identifier,confirmed=True))


if __name__=='__main__':unittest.main()
