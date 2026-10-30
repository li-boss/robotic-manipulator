"""九点用户报告映射：合成照片、临时文件和假控制器，绝不连接硬件。"""
import copy
import json
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import test_teaching_cards as card_fixtures
from test_control import Fake
from teaching_plan import build, fit, interpolate, mapping_fit, mapping_samples, readiness

FAKE_WRITE=Fake.write


class ReportedCardsTests(unittest.TestCase):
    tearDown=card_fixtures.CardsTests.tearDown
    image=card_fixtures.CardsTests.image
    fields=card_fixtures.CardsTests.fields
    capture=card_fixtures.CardsTests.capture
    target=card_fixtures.CardsTests.target

    def setUp(self):
        card_fixtures.CardsTests.setUp(self)
        self.demo.cards=self.cards
        cfg=self.teach.load();cfg.update(task_mode='pickup_only',common={},orders={})
        self.teach.path.write_text(json.dumps(cfg))
        for index,(x,y) in enumerate([(x,y) for y in [80,240,400] for x in [120,320,520]],1):
            self.target(index,(x,y))
            point=self.cards.status()['slots'][index]['target']['pixel']
            self.cards.confirm(self.fields(index,'target',pixel=point,confirmed=True))
            for phase,grip in [('above',80),('grasp',130)]:
                self.cards.angles(self.fields(index,phase,angles=[90+(x-320)//40,30+y//20,40,40,90,grip],
                    order=[1,5,4,3,2,6],motion_ms=1000))

    def fit_fields(self,**extra):
        return dict(expected_cards_sha256=self.cards.sha(),expected_sha256=self.teach.sha(),
                    user_reported_verified=True,scene_confirmed=True,**extra)

    def establish(self):
        return self.cards.fit_reported(self.fit_fields())

    def test_fit_without_receipts_or_hardware_preserves_all_existing_data(self):
        before_cards=self.cards.raw();before_states=self.control.states.raw();before=self.teach.load()
        before_files={p.name:p.read_bytes() for p in self.demo.auto.samples.iterdir()}
        with (patch.object(self.control,'factory',side_effect=AssertionError('hardware')),
              patch.object(self.teach,'capture',side_effect=AssertionError('capture'))):
            result=self.establish()
        cfg=result['teaching']['setup']
        self.assertEqual(cfg['samples'],before['samples']);self.assertEqual(cfg['validations'],before['validations'])
        self.assertEqual(cfg['fit'],before['fit']);self.assertFalse(cfg['reference_verified'])
        self.assertEqual(self.cards.raw(),before_cards);self.assertEqual(self.control.states.raw(),before_states)
        self.assertEqual(before_files,{p.name:p.read_bytes() for p in self.demo.auto.samples.iterdir()})
        self.assertFalse((self.teach.path.parent/'logs/teaching').exists())
        self.assertTrue(result['teaching']['readiness']['ready'])
        self.assertTrue(all(not s.get('verified') and 'receipts' not in s and 'evidence' not in s for s in mapping_samples(cfg)))
        self.assertEqual(Fake.calls,[])

    def test_vertex_targets_and_internal_interpolation_without_independent_validation(self):
        cfg=self.establish()['teaching']['setup']
        for sample in mapping_samples(cfg):
            calculated=interpolate(cfg,self.arms,sample['pixel'])
            self.assertEqual(calculated['above'],sample['above']);self.assertEqual(calculated['grasp'],sample['grasp'])
        result=interpolate(cfg,self.arms,[240,160])
        self.assertEqual(result['above'],[88,38,40,40,90])
        plan=build(cfg,self.arms,[240,160]);self.assertEqual(len(plan['steps']),12)
        self.assertEqual(plan['steps'][-1]['joints'],[[6,130]])
        self.assertEqual([s['joints'][0][0] for s in plan['steps']],[1,5,4,3,2,6]*2)
        with self.assertRaises(ValueError):build(cfg,self.arms,[10,10])

    def test_report_and_versions_required_and_no_partial_save(self):
        before=self.teach.path.read_bytes()
        for change in [dict(user_reported_verified=False),dict(scene_confirmed=False),
                       dict(expected_sha256='changed'),dict(expected_cards_sha256='changed')]:
            fields=self.fit_fields();fields.update(change)
            with self.assertRaises(ValueError):self.cards.fit_reported(fields)
        self.assertEqual(self.teach.path.read_bytes(),before);self.assertEqual(Fake.calls,[])

    def test_missing_photo_invalid_target_and_inconsistent_recipe_rejected(self):
        original=self.cards.raw();original_states=self.control.states.raw();before=self.teach.path.read_bytes()
        target=self.cards.load()['slots'][1]['target']['frame_id']
        path=self.demo.auto.samples/(target+'.jpg');photo=path.read_bytes();path.unlink()
        with self.assertRaises((ValueError,OSError)):self.establish()
        path.write_bytes(photo)
        for key,value in [('angles',[90,34,40,40,89,80]),('order',[2,1,5,4,3,6]),('motion_ms',1200)]:
            cards=json.loads(original);states=json.loads(original_states)
            plan=cards['slots'][1]['plans']['above'];plan[key]=value
            saved=next(s for s in states['states'] if s['id']==plan['id']);saved[key]=value
            self.cards.path.write_text(json.dumps(cards));self.control.states.path.write_text(json.dumps(states))
            with self.assertRaises(ValueError):self.establish()
        self.assertEqual(self.teach.path.read_bytes(),before);self.assertEqual(Fake.calls,[])

    def test_source_change_invalidates_status_and_prevents_execution(self):
        cfg=self.establish()['teaching']['setup'];self.assertTrue(readiness(cfg,self.arms)['ready'])
        cards=self.cards.load();cards['slots'][1]['pixel'][0]+=1;self.cards.path.write_text(json.dumps(cards))
        self.assertFalse(self.teach.status()['readiness']['ready'])
        with self.assertRaises(ValueError):self.teach.bound(self.teach.load())
        self.assertEqual(Fake.calls,[])

    def test_photo_modification_and_config_change_invalidate_mapping(self):
        cfg=self.establish()['teaching']['setup']
        sample=mapping_samples(cfg)[0];path=self.demo.auto.samples/(sample['target_frame']+'.jpg')
        original=path.read_bytes();path.write_bytes(original+b'changed')
        self.assertFalse(self.teach.status()['readiness']['ready']);path.write_bytes(original)
        changed=copy.deepcopy(self.arms);changed['motion_ms']=2000;self.arm_path.write_text(json.dumps(changed))
        self.assertFalse(self.teach.status()['readiness']['ready'])

    def test_measured_mode_still_requires_real_verification(self):
        cfg=self.establish()['teaching']['setup']
        measured=copy.deepcopy(cfg);measured.pop('mapping_mode');measured['samples']=mapping_samples(cfg)
        with self.assertRaises(ValueError):fit(measured,self.arms)
        self.assertIsNone(cfg['fit']);self.assertIsNotNone(mapping_fit(cfg))

    def test_preview_identifies_new_position_without_capture_or_implicit_motion(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((220,160))
        with patch.object(self.demo.auto,'capture',side_effect=AssertionError('capture')):
            result=self.teach.preview(dict(action='auto',scene_confirmed=True))
        np.testing.assert_allclose(result['target']['pixel'],[220,160],atol=1)
        self.assertEqual(result['parameters']['plan']['mapping_mode'],'reported_cards')
        self.assertEqual(Fake.calls,[])
        # 来源被修改后，即使仍有旧确认令牌，也不能通过执行核对。
        cards=self.cards.load();cards['slots'][2]['center_confirmed']=False;self.cards.path.write_text(json.dumps(cards))
        with self.assertRaises(ValueError):self.teach.validate_execution(self.control.pending)
        self.assertEqual(Fake.calls,[])

    def test_no_target_multiple_targets_bad_start_do_not_move(self):
        self.establish()
        for kind in ['empty','multiple','wrong_start']:
            Fake.angles=[90,50,40,40,89,80];Fake.read_fail=False
            self.image(None if kind=='empty' else (220,160))
            if kind=='multiple':
                image=cv2.imdecode(np.frombuffer(self.camera.jpeg,np.uint8),cv2.IMREAD_COLOR)
                cv2.rectangle(image,(350,300),(390,340),(0,255,255),-1)
                self.camera.jpeg=cv2.imencode('.jpg',image)[1].tobytes();self.camera.sequence+=1
            if kind=='wrong_start':Fake.angles[0]=110
            with self.assertRaises(ValueError):self.teach.preview(dict(action='auto',scene_confirmed=True))
        Fake.read_fail=False;self.assertEqual(Fake.calls,[])

    def wait_finished(self):
        for _ in range(1000):
            if not self.control.busy:break
            time.sleep(.001)
        self.assertFalse(self.control.busy)

    def tracked_write(self,controller,pairs):
        FAKE_WRITE(controller,pairs)
        for joint,value in pairs:Fake.angles[joint-1]=value

    def test_missing_readings_at_preview_start_and_every_step_continue_without_retry(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        read_calls=[]
        def missing(controller):
            read_calls.append(1)
            return [dict(id=i,angle=None,read_ok=False,error='intermittent I2C') for i in range(1,7)]
        with patch.object(Fake,'read_report',missing),patch.object(Fake,'write',lambda controller,pairs:self.tracked_write(controller,pairs)):
            with self.assertRaises(ValueError):self.teach.preview(dict(action='auto',scene_confirmed=False))
            self.assertEqual(read_calls,[],'manual observation confirmation required before optional reads')
            result=self.teach.preview(dict(action='auto',scene_confirmed=True))
            self.assertTrue(all(row['angle'] is None and row['read_ok'] is False for row in result['joints']))
            self.assertEqual(len(result['read_warnings']),6);self.assertIn('读数失败',result['conditions'])
            self.assertEqual(Fake.calls,[],'preview does not move')
            self.control.execute(dict(token=result['token'],confirmed=True));self.wait_finished()
        self.assertEqual(self.control.job['status'],'completed')
        self.assertEqual(len(Fake.calls),12);self.assertEqual(Fake.calls[-1],[(6,130)])
        self.assertEqual(len(read_calls),14,'one read batch per stage, no repeated reads')
        self.assertEqual(len(self.control.job['read_warnings']),84)
        self.assertTrue(all(row['angle'] is None for row in self.control.rows),'no invented telemetry')
        events=[json.loads(line) for line in self.demo.store.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events[-1]['result'],'unconfirmed','missing reads cannot certify a grip')
        self.assertEqual(len(events[-1]['read_warnings']),84)
        self.control.execute(dict(token=result['token'],confirmed=True))
        self.assertEqual(len(Fake.calls),12,'duplicate request does not repeat motion')

    def test_failed_step_read_then_valid_deviation_still_stops(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        original=Fake.read_report;count=[0]
        def reports(controller):
            count[0]+=1
            if count[0]==3:return [dict(id=i,angle=None,read_ok=False,error='failed after step 1') for i in range(1,7)]
            rows=original(controller)
            if count[0]==4:rows[1]['angle']+=10
            return rows
        with patch.object(Fake,'read_report',reports),patch.object(Fake,'write',lambda controller,pairs:self.tracked_write(controller,pairs)):
            result=self.teach.preview(dict(action='auto',scene_confirmed=True))
            self.control.execute(dict(token=result['token'],confirmed=True));self.wait_finished()
        self.assertEqual(self.control.job['status'],'failed')
        self.assertEqual(len(Fake.calls),2,'read failure continues, successful mismatch stops next commands')
        self.assertIn('偏差',self.control.job['error'])

    def test_failed_preview_joint_recovers_to_wrong_start_and_blocks_motion(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        original=Fake.read_report;count=[0]
        def reports(controller):
            count[0]+=1;rows=original(controller)
            if count[0]==1:rows[0].update(angle=None,read_ok=False,error='I2C')
            else:rows[0]['angle']=110
            return rows
        with patch.object(Fake,'read_report',reports):
            result=self.teach.preview(dict(action='auto',scene_confirmed=True))
            self.control.execute(dict(token=result['token'],confirmed=True));self.wait_finished()
        self.assertEqual(self.control.job['status'],'failed');self.assertEqual(Fake.calls,[])

    def test_optional_read_policy_does_not_relax_real_receipts_or_invalid_reports(self):
        self.establish();Fake.read_fail=True
        with self.assertRaises(ValueError):self.control.read_rows(self.arms)
        Fake.read_fail=False;Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        for rows in [[],[dict(id=i,angle=90,read_ok=True) for i in range(6,0,-1)],
                     [dict(id=i,angle=999 if i==1 else 90,read_ok=True) for i in range(1,7)]]:
            with patch.object(Fake,'read_report',return_value=rows):
                with self.assertRaises(ValueError):self.teach.preview(dict(action='auto',scene_confirmed=True))
        self.assertEqual(Fake.calls,[])

    def test_execution_failure_stops_without_retry_reset_or_release(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((220,160))
        result=self.teach.preview(dict(action='auto',scene_confirmed=True));Fake.fail=True
        self.control.execute(dict(token=result['token'],confirmed=True))
        for _ in range(1000):
            if not self.control.busy:break
            time.sleep(.001)
        self.assertEqual(self.control.job['status'],'failed');self.assertEqual(len(Fake.calls),1)
        repeated=self.control.execute(dict(token=result['token'],confirmed=True))
        self.assertEqual(repeated['job']['status'],'failed')
        self.assertEqual(len(Fake.calls),1)

    def test_complete_execution_clamps_once_and_does_not_return_or_repeat(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        result=self.teach.preview(dict(action='auto',scene_confirmed=True))
        original=Fake.write
        def tracked(controller,pairs):
            original(controller,pairs)
            for joint,value in pairs:Fake.angles[joint-1]=value
        with patch.object(Fake,'write',tracked):
            self.control.execute(dict(token=result['token'],confirmed=True))
            for _ in range(1000):
                if not self.control.busy:break
                time.sleep(.001)
        self.assertEqual(self.control.job['status'],'completed')
        self.assertEqual(len(Fake.calls),12);self.assertEqual(Fake.calls[-1],[(6,130)])
        self.assertEqual(Fake.angles,[88,38,40,40,90,130])
        repeated=self.control.execute(dict(token=result['token'],confirmed=True))
        self.assertEqual(repeated['job']['status'],'completed')
        self.assertEqual(len(Fake.calls),12)

    def test_confirmed_preview_executes_without_any_live_camera_recheck(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        result=self.teach.preview(dict(action='auto',scene_confirmed=True))
        frozen=copy.deepcopy(result['parameters']['preview_target'])
        self.assertEqual(result['parameters']['target_policy'],'confirmed_preview')
        self.image(None)  # 后续画面已不同；执行仍必须用人工确认的冻结目标。
        with (patch.object(self.demo.auto,'frame',side_effect=AssertionError('must not read camera after confirmation')),
              patch.object(self.teach,'target',side_effect=ValueError('存在其他有色目标')),
              patch.object(Fake,'write',lambda controller,pairs:self.tracked_write(controller,pairs))):
            with self.assertRaises(ValueError):self.control.execute(dict(token=result['token'],confirmed=False))
            self.assertEqual(Fake.calls,[])
            self.control.execute(dict(token=result['token'],confirmed=True));self.wait_finished()
        self.assertEqual(self.control.job['status'],'completed')
        self.assertEqual(len(Fake.calls),12);self.assertEqual(Fake.calls[-1],[(6,130)])
        task=json.loads(self.demo.store.path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(task['target_reference'],frozen)
        self.assertEqual(task['target_policy'],'confirmed_preview')
        self.control.execute(dict(token=result['token'],confirmed=True));self.assertEqual(len(Fake.calls),12)

    def test_confirmed_preview_does_not_allow_changed_center_plan_or_confirmation(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        self.teach.preview(dict(action='auto',scene_confirmed=True))
        original=copy.deepcopy(self.control.pending)
        for kind in ['center','step','illegal','frame','confirmation']:
            candidate=copy.deepcopy(original)
            if kind=='center':candidate['target']['pixel'][0]+=1
            if kind=='step':candidate['parameters']['plan']['steps'][0]['motion_ms']+=500
            if kind=='illegal':candidate['parameters']['plan']['steps'][-1]['joints'][0][1]=999
            if kind=='frame':candidate['target']['frame_id']+=1
            if kind=='confirmation':candidate['parameters']['scene_confirmed']=False
            with self.assertRaises(ValueError):self.teach.validate_execution(candidate)
        self.assertEqual(Fake.calls,[])

    def test_old_execution_mode_still_rechecks_live_target(self):
        self.establish();Fake.angles=[90,50,40,40,89,80];self.image((240,160))
        self.teach.preview(dict(action='auto',scene_confirmed=True))
        candidate=copy.deepcopy(self.control.pending);candidate['parameters'].pop('target_policy')
        with patch.object(self.teach,'recheck_target',side_effect=ValueError('live target changed')) as checker:
            with self.assertRaisesRegex(ValueError,'live target changed'):self.teach.validate_execution(candidate)
        checker.assert_called_once();self.assertEqual(Fake.calls,[])


if __name__=='__main__':unittest.main()
