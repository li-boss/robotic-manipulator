"""合成场景、内存相机与假控制器；禁止实际硬件连接。"""
import copy
from contextlib import nullcontext
import json
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from control import Control
from core import EventStore,arm
from auto_task import AutoTask
from teaching_task import Teaching
from teaching_plan import defaults,digest,fit,locate,interpolate,build,ROUTES,readiness
from teaching_vision import top,matches,background
from test_control import Fake


def fixture():
    arms=dict(hardware_ready=True,motion_ms=1500,settle_ms=300,
        joint_limits={str(i):[90,250] if i==5 else [10,170] for i in range(1,7)},
        gripper=dict(open=80,closed=130),poses={},targets={})
    cfg=defaults();cfg['arm_config_sha256']=digest(arms)
    cfg['baseline']=dict(frame_id='a'*32,angles=[90,50,40,40,90],readings=[90,50,40,40,89],image_size=[640,480])
    cfg['common']={name:dict(frame_id='a'*32,angles=[90,50,40,40,90]) for name in ['transfer','place_above','place']}
    cfg['orders']={name:[1,2,3,4,5] for name in ROUTES};cfg['reference_verified']=True
    for i,(x,y) in enumerate((x,y) for y in [100,240,380] for x in [120,320,520]):
        cfg['samples'].append(dict(id=str(i),pixel=[x,y],above=[90,50+x//40,40,40,90],
            grasp=[90,40+y//40,40,40,90],verified=True,
            descriptor=dict(area=1600,width=40,height=40,angle=0,hue=30)))
    cfg['fit']=fit(cfg,arms)
    cfg['validations']=[dict(id=str(i),cell_id=c['id'],pixel=np.mean([cfg['samples'][j]['pixel'] for j in c['indices']],axis=0).tolist(),
        passed=True,fit_version=cfg['fit']['version']) for i,c in enumerate(cfg['fit']['cells'])]
    return cfg,arms


class MappingTests(unittest.TestCase):
    def test_default_matches_file_and_is_blocked(self):
        self.assertEqual(defaults(),json.loads((Path(__file__).parent/'teaching_setup.json').read_text(encoding='utf-8')))
        self.assertFalse(readiness(defaults(),fixture()[1])['ready'])

    def test_triangulation_interpolation_and_integer_plan(self):
        cfg,arms=fixture();pixel=[200,160]
        predicted=interpolate(cfg,arms,pixel)
        self.assertEqual(predicted['above'],[90,55,40,40,90])
        plan=build(cfg,arms,pixel);self.assertEqual(len(plan['steps']),7)
        self.assertTrue(all(len(s['joints'])==1 and type(s['joints'][0][1])is int for s in plan['steps']))
        self.assertEqual(plan['steps'][-1]['joints'],[[6,80]])

    def test_hull_edge_and_no_extrapolation(self):
        cfg,arms=fixture();self.assertIsNotNone(locate(cfg,[120,100]))
        for p in [[119,100],[600,400],[-1,240]]:
            with self.assertRaises(ValueError):interpolate(cfg,arms,p)

    def test_duplicate_collinear_and_insufficient_samples(self):
        for change in ['duplicate','line','few']:
            cfg,arms=fixture()
            if change=='duplicate':cfg['samples'][1]['pixel']=cfg['samples'][0]['pixel']
            if change=='line':
                for i,s in enumerate(cfg['samples']):s['pixel']=[20+i*20,100]
            if change=='few':cfg['samples'].pop()
            with self.assertRaises(ValueError):fit(cfg,arms)

    def test_validation_gate_minimum_five_and_specific_cell(self):
        cfg,arms=fixture();point=[200,160]
        cfg['validations']=cfg['validations'][:4]
        with self.assertRaises(ValueError):interpolate(cfg,arms,point)
        cfg,arms=fixture();cell,_=locate(cfg,point)
        cfg['validations']=[v for v in cfg['validations'] if v['cell_id']!=cell['id']]
        with self.assertRaises(ValueError):interpolate(cfg,arms,point)

    def test_sample_and_arm_changes_invalidate(self):
        cfg,arms=fixture();cfg['samples'][0]['grasp'][1]+=1
        with self.assertRaises(ValueError):interpolate(cfg,arms,[200,160])
        cfg,arms=fixture();arms['motion_ms']=2000
        with self.assertRaises(ValueError):interpolate(cfg,arms,[200,160])

    def test_six_joint_bounds_and_invalid_values(self):
        cfg,arms=fixture()
        for joint in range(1,7):
            lower,upper=arms['joint_limits'][str(joint)]
            for value in [lower,upper]:self.assertEqual(arm.angle(arms,joint,value),value)
            for value in [lower-1,upper+1,None,True,'90',float('nan'),float('inf'),90.5]:
                with self.assertRaises((ValueError,TypeError)):arm.angle(arms,joint,value)
        arms['joint_limits']['5']=[89,250]
        with self.assertRaises(ValueError):build(cfg,arms,[200,160])

    def test_raw89_is_observation_only(self):
        cfg,arms=fixture();self.assertEqual(cfg['baseline']['readings'][4],89)
        plan=build(cfg,arms,[200,160]);self.assertTrue(all(s['joints'][0][1]>=90 for s in plan['steps'] if s['joints'][0][0]==5))
        cfg['baseline']['angles'][4]=89
        with self.assertRaises(ValueError):build(cfg,arms,[200,160])

    def test_continuous_out_of_range_not_hidden_by_rounding(self):
        cfg,arms=fixture()
        cfg['samples'][0]['above'][0]=9.9;cfg['fit']['version']=digest(__import__('teaching_plan').mapping_source(cfg))
        with self.assertRaises(ValueError):interpolate(cfg,arms,cfg['samples'][0]['pixel'])

    def test_boolean_pixel_and_unconfirmed_sample_rejected(self):
        cfg,arms=fixture()
        with self.assertRaises(ValueError):interpolate(cfg,arms,[True,160])
        cfg['samples'][0]['verified']=False
        with self.assertRaises(ValueError):fit(cfg,arms)


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.vision=dict(vision_reviewed=True,colors=dict(yellow=[[20,100,100],[40,255,255]]),min_area=200)
        self.image=np.full((480,640,3),240,np.uint8);cv2.rectangle(self.image,(180,140),(220,180),(0,255,255),-1)

    def test_single_square_and_appearance(self):
        target=top(self.image,self.vision);np.testing.assert_allclose(target['pixel'],[200,160],atol=1)
        matches(target,[target])
        bad=copy.deepcopy(target);bad['descriptor']['area']*=2
        with self.assertRaises(ValueError):matches(bad,[target])

    def test_multiple_unknown_and_unreviewed(self):
        image=self.image.copy();cv2.rectangle(image,(300,300),(340,340),(0,255,255),-1)
        with self.assertRaises(ValueError):top(image,self.vision)
        image=self.image.copy();cv2.rectangle(image,(300,300),(340,340),(255,0,0),-1)
        with self.assertRaises(ValueError):top(image,self.vision)
        self.vision['vision_reviewed']=False
        with self.assertRaises(ValueError):top(self.image,self.vision)

    def test_featureless_requires_manual_scene_confirmation(self):
        result=background(np.full_like(self.image,240),self.image,self.vision)
        self.assertTrue(result['manual'])

    def test_textured_background_drift_rejected(self):
        image=np.full_like(self.image,240)
        for x in range(40,600,40):
            for y in range(40,450,40):cv2.rectangle(image,(x,y),(x+8,y+8),(0,0,0),-1)
        self.assertFalse(background(image,image,self.vision)['manual'])
        shifted=cv2.warpAffine(image,np.array([[1,0,7],[0,1,5]],float),(640,480))
        with self.assertRaises(ValueError):background(image,shifted,self.vision)


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'arm_playground').mkdir();(self.root/'camera_preview').mkdir();(self.root/'sorting_demo').mkdir()
        for n in ['AGENTS.md','机械臂操作准则.md','arm_playground/pose_setup.py','arm_playground/joint_check.py','camera_preview/server.py']:
            (self.root/n).write_text('synthetic audit fixture',encoding='utf-8')
        self.cfg,self.arms=fixture();self.arm_path=self.root/'arm_playground/config.json';self.arm_path.write_text(json.dumps(self.arms))
        self.control=Control(self.arm_path,real=True,factory=Fake,root=self.root,lock_factory=nullcontext)
        self.demo=SimpleNamespace(control=self.control,config_path=self.root/'sorting_demo/config.json',
            config=dict(vision_reviewed=True,colors=dict(yellow=[[20,100,100],[40,255,255]]),min_area=200),
            store=EventStore(self.root/'sorting_demo/logs/events.jsonl'))
        self.demo.config_path.write_text(json.dumps(self.demo.config));self.demo.auto=AutoTask(self.demo)
        self.teach=Teaching(self.demo,planner=lambda args:build(args['setup'],args['arms'],args['pixel'],args.get('pair'),args.get('require_validated',True)))
        self.teach.path.write_text(json.dumps(self.cfg));self.control.extra_audit_paths.append(self.teach.path)
        Fake.calls=[];Fake.fail=False;Fake.read_fail=False;Fake.wait=None;Fake.angles=[90,50,40,40,89,80]

    def tearDown(self):
        for _ in range(1000):
            if not self.control.busy:break
            time.sleep(.001)
        self.tmp.cleanup()

    def test_status_and_load_do_not_initialize_controller(self):
        with patch.object(self.control,'factory',side_effect=AssertionError('hardware')):
            self.assertTrue(self.teach.status()['readiness']['ready'])

    def test_save_backup_and_version_conflict(self):
        before=self.teach.path.read_bytes();sha=self.teach.sha()
        cfg=self.teach.load();cfg['fit']=None
        self.teach.save(cfg,sha,'test_save')
        self.assertEqual(next(self.teach.path.parent.glob('teaching_setup.before_*.json')).read_bytes(),before)
        with self.assertRaises(ValueError):self.teach.save(cfg,sha,'stale')
        self.assertEqual(Fake.calls,[])

    def test_sample_change_clears_mapping_and_validation(self):
        result=self.teach.samples(dict(action='delete',sample_id='0',expected_sha256=self.teach.sha()))
        self.assertIsNone(result['setup']['fit']);self.assertEqual(result['setup']['validations'],[])
        self.assertEqual(Fake.calls,[])

    def test_no_boolean_only_validation_and_no_simulated_validation(self):
        item=dict(id='b'*32,case='sample',saved=False,sha256=self.teach.sha(),plan=build(self.cfg,self.arms,[200,160]),sample={},
            empty=dict(status='pending',next=0,evidence=[]),loaded=dict(status='pending',next=0,evidence=[]))
        self.teach.drafts[item['id']]=item;item['hashes']=self.control.audit({})['hashes']
        with self.assertRaises(ValueError):self.teach.persist_verified(dict(draft_id=item['id'],success=True,expected_sha256=self.teach.sha()),'sample')
        self.control.real=False
        with self.assertRaises(ValueError):self.teach.persist_verified(dict(draft_id=item['id'],success=True,expected_sha256=self.teach.sha()),'sample')

    def test_proof_file_tampering_rejected(self):
        with patch.object(self.teach,'evidence',return_value={}):
            with self.assertRaises((ValueError,FileNotFoundError)):self.teach.bound(self.cfg)

    def test_plan_preflight_rejects_invalid_later_step_no_writes(self):
        plan=dict(steps=[dict(joints=[[1,100]],motion_ms=1500,settle_ms=300),dict(joints=[[5,89]],motion_ms=1500,settle_ms=300)])
        with patch.object(self.teach,'validate_execution'):
            with self.assertRaises(ValueError):self.teach.run(Fake(self.arms),dict(token='x',parameters=dict(plan=plan)))
        self.assertEqual(Fake.calls,[])

    def test_exception_has_no_followup_or_cleanup(self):
        plan=dict(steps=[dict(joints=[[1,100]],motion_ms=1500,settle_ms=300),dict(joints=[[2,60]],motion_ms=1500,settle_ms=300)])
        p=dict(token='x',parameters=dict(plan=plan),rows=Fake(self.arms).read_report(),audit=self.control.audit({}))
        Fake.fail=True
        with patch.object(self.teach,'validate_execution'):
            with self.assertRaises(RuntimeError):self.teach.run(Fake(self.arms),p)
        self.assertEqual(Fake.calls,[[(1,100)]])

    def test_complete_plan_confirmation_dedup_and_result(self):
        class Updating(Fake):
            def write(self,pairs):
                super().write(pairs)
                for joint,value in pairs:Fake.angles[joint-1]=value
        self.control.factory=Updating
        plan=dict(steps=[dict(joints=[[1,100]],motion_ms=1500,settle_ms=300),dict(joints=[[2,60]],motion_ms=1500,settle_ms=300)])
        args=dict(operation='teaching',plan=plan,scene_confirmed=True)
        p=dict(token='finite',parameters=args,expires=time.monotonic()+120,rows=Updating(self.arms).read_report(),audit=self.control.audit(args))
        self.control.pending=p
        with patch.object(self.teach,'validate_execution'):
            with self.assertRaises(ValueError):self.control.execute(dict(token='finite',confirmed=False))
            self.control.execute(dict(token='finite',confirmed=True))
            for _ in range(1000):
                if not self.control.busy:break
                time.sleep(.001)
            self.control.execute(dict(token='finite',confirmed=True))
        self.assertEqual(Fake.calls,[[(1,100)],[(2,60)]])
        self.assertEqual(self.control.job['status'],'completed')
        self.assertEqual(self.teach.samples(dict(action='result',task_id='finite',result='success'))['result'],'success')

    def test_step_order_failed_state_and_changed_start(self):
        item=dict(id='d'*32,case='validation',plan=build(self.cfg,self.arms,[200,160]),sample=dict(pixel=[200,160]),
            saved=False,sha256=self.teach.sha(),empty=dict(status='pending',next=0,evidence=[]),loaded=dict(status='pending',next=0,evidence=[]))
        self.teach.drafts[item['id']]=item;item['hashes']=self.control.audit({})['hashes']
        args=dict(action='step',draft_id=item['id'],mode='empty',step=1,scene_confirmed=True,empty_confirmed=True)
        with self.assertRaises(ValueError):self.teach.step_preview(args)
        args['step']=0
        result=self.teach.step_preview(args)
        self.assertEqual(result['parameters']['teaching_step']['step'],0)
        self.assertEqual(Fake.calls,[])
        Fake.angles[0]=130
        with self.assertRaises(ValueError):self.teach.step_preview(args)
        self.assertIsNone(self.control.pending)

    def test_target_change_rejects_confirmation(self):
        with patch.object(self.teach,'target',return_value=dict(pixel=[230,160],descriptor=dict(width=40))):
            with self.assertRaises(ValueError):self.teach.recheck_target(self.cfg,dict(pixel=[200,160]),True)
        self.assertEqual(Fake.calls,[])

    def test_capture_defaults_rejects_invalid_target_before_hardware(self):
        with patch.object(self.control,'factory',side_effect=AssertionError('hardware')):
            with self.assertRaises(ValueError):self.teach.capture(dict(kind='reference',angles=[90,50,40,40,89],expected_sha256=self.teach.sha()))

    def test_full_capture_step_verification_persists_proof_and_preserves_raw89(self):
        class Updating(Fake):
            def write(self,pairs):
                super().write(pairs)
                for joint,value in pairs:Fake.angles[joint-1]=value
        self.control.factory=Updating
        camera=SimpleNamespace(condition=threading.RLock(),sequence=1,updated=time.monotonic(),
            status=lambda:dict(connected=True))
        def image(yellow=False):
            frame=np.full((480,640,3),240,np.uint8)
            if yellow:cv2.rectangle(frame,(180,140),(220,180),(0,255,255),-1)
            camera.jpeg=cv2.imencode('.jpg',frame)[1].tobytes();camera.updated=time.monotonic();camera.sequence+=1
        self.demo.camera=camera;image()
        common_pose=[90,50,40,40,90]
        for kind in ['reference','transfer','place_above','place']:
            self.teach.capture(dict(kind=kind,angles=common_pose,expected_sha256=self.teach.sha()))
        self.teach.orders(dict(orders={r:[1,2,3,4,5] for r in ROUTES},expected_sha256=self.teach.sha()))
        image(True);target=self.teach.capture(dict(kind='target',expected_sha256=self.teach.sha()))
        receipts={}
        for kind,value in [('above',55),('grasp',45)]:
            time.sleep(.002);image(True);Fake.angles[1]=value
            receipts[kind]=self.teach.capture(dict(kind=kind,angles=[90,value,40,40,90],expected_sha256=self.teach.sha()))['id']
        source=self.teach.sample_draft(dict(target_frame=target['id'],pixel=target['target']['pixel'],
            above_frame=receipts['above'],grasp_frame=receipts['grasp'],center_confirmed=True,expected_sha256=self.teach.sha()))
        draft=source['draft'];Fake.angles=[90,50,40,40,89,80]
        with patch.object(self.teach,'recheck_target',return_value={}):
            for mode in ['empty','loaded']:
                for i in range(len(draft['plan']['steps'])):
                    preview=self.teach.step_preview(dict(draft_id=draft['id'],mode=mode,step=i,scene_confirmed=True,empty_confirmed=True))
                    self.control.execute(dict(token=preview['token'],confirmed=True))
                    for _ in range(1000):
                        if not self.control.busy:break
                        time.sleep(.001)
                    self.assertEqual(self.control.job['status'],'completed')
        result=self.teach.persist_verified(dict(draft_id=draft['id'],success=True,expected_sha256=self.teach.sha()),'sample')
        self.assertEqual(len(result['setup']['samples']),1)
        self.assertEqual(result['setup']['baseline']['readings'][4],89)
        self.assertEqual(result['setup']['samples'][0]['above'],[90,55,40,40,90])
        self.teach.bound(result['setup'])
        proof=self.teach.path.parent/'logs/teaching'/result['setup']['samples'][0]['evidence']['file']
        stored=json.loads(proof.read_text(encoding='utf-8'));self.assertEqual(stored['loaded']['status'],'completed')
        self.assertEqual(len(Fake.calls),14)

    def test_wrong_ros_output_never_becomes_pending(self):
        self.teach.planner=lambda args:dict(steps=[dict(joints=[[5,89]])])
        with self.assertRaises(ValueError):self.teach.checked_plan(self.cfg,self.arms,[200,160],None,True)
        self.assertIsNone(self.control.pending);self.assertEqual(Fake.calls,[])

    def test_controller_not_ready_and_busy_rejected(self):
        self.control.busy=True
        with self.assertRaises(ValueError):self.teach.preview(dict(action='auto',scene_confirmed=True))
        self.control.busy=False
        self.arms['hardware_ready']=False;self.arm_path.write_text(json.dumps(self.arms))
        cfg=copy.deepcopy(self.cfg);cfg['arm_config_sha256']=digest(self.arms);cfg['fit']=fit(cfg,self.arms)
        self.teach.path.write_text(json.dumps(cfg))
        with patch.object(self.teach,'bound'),patch.object(self.control,'factory',side_effect=AssertionError('hardware')):
            with self.assertRaises(ValueError):self.teach.preview(dict(action='auto',scene_confirmed=True))

    def test_teaching_http_requires_same_origin_and_csrf(self):
        from server import demo_handler
        self.demo.camera=SimpleNamespace();self.demo.teaching=self.teach
        handler=demo_handler(self.demo)
        for origin,csrf,expected in [(None,self.control.csrf,403),('http://other',self.control.csrf,403),
                                     ('http://localhost:8080','wrong',403),('http://localhost:8080',self.control.csrf,200)]:
            payload=json.dumps(dict(kind='target',csrf=csrf)).encode();responses=[]
            request=SimpleNamespace(path='/teaching/capture',headers=dict(Host='localhost:8080',Origin=origin,
                **{'Content-Type':'application/json','Content-Length':str(len(payload))}),rfile=io.BytesIO(payload),
                send_json=lambda code,value:responses.append(code))
            with patch.object(self.teach,'capture',return_value=dict(fake=True)) as capture:
                handler.do_POST(request)
                self.assertEqual(responses,[expected]);self.assertEqual(capture.call_count,1 if expected==200 else 0)


if __name__=='__main__':unittest.main()
