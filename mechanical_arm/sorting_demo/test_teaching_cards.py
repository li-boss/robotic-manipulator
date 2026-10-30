"""仅合成图片、内存相机和假设备；不连接实机。"""
from contextlib import nullcontext
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from teaching_cards import TeachingCards
from teaching_plan import defaults, ROUTES
import test_teaching as fixtures
from test_control import Fake


class CardsTests(unittest.TestCase):
    def setUp(self):
        fixtures.BusinessTests.setUp(self)
        self.demo.teaching=self.teach
        self.cards=TeachingCards(self.demo)
        self.teach.path.write_text(json.dumps(defaults()))
        self.camera=SimpleNamespace(condition=threading.RLock(),sequence=0,updated=time.monotonic(),status=lambda:dict(connected=True))
        self.demo.camera=self.camera
        self.image()
        for kind in ['reference','transfer','place_above','place']:
            self.teach.capture(dict(kind=kind,angles=[90,50,40,40,90],expected_sha256=self.teach.sha()))
        self.teach.orders(dict(orders={r:[1,2,3,4,5] for r in ROUTES},expected_sha256=self.teach.sha()))

    tearDown=fixtures.BusinessTests.tearDown

    def image(self,point=None):
        time.sleep(.002)
        image=np.full((480,640,3),240,np.uint8)
        if point:cv2.rectangle(image,(point[0]-20,point[1]-20),(point[0]+20,point[1]+20),(0,255,255),-1)
        self.camera.jpeg=cv2.imencode('.jpg',image)[1].tobytes();self.camera.updated=time.monotonic();self.camera.sequence+=1

    def fields(self,index,phase,**extra):
        return dict(slot=index,phase=phase,expected_cards_sha256=self.cards.sha(),expected_sha256=self.teach.sha(),**extra)

    def capture(self,index,phase,angles=None):
        return self.cards.record(self.fields(index,phase,angles=angles))

    def target(self,index=1,point=(200,160)):
        Fake.angles=[90,50,40,40,89,80];self.image(point)
        self.capture(index,'target')

    def test_ten_cards_load_without_hardware_and_import_existing_baseline(self):
        with patch.object(self.control,'factory',side_effect=AssertionError('hardware')):
            status=self.cards.status()
        self.assertEqual(len(status['slots']),10)
        self.assertEqual(status['slots'][0]['reference']['angles'],[90,50,40,40,90,None])
        self.assertFalse(self.cards.path.exists());self.assertEqual(Fake.calls,[])

    def test_independent_photos_angles_persist_and_confirm_and_plan(self):
        self.target();self.target(2,(340,160))
        two=self.cards.status()['slots'][2]['target']['frame_id']
        point=self.cards.status()['slots'][1]['target']['pixel']
        self.cards.confirm(self.fields(1,'target',pixel=point,confirmed=True))
        for phase,value in [('above',55),('grasp',45)]:
            Fake.angles[1]=value;Fake.angles[5]=80 if phase=='above' else 130
            self.image((200,160));self.capture(1,phase,[90,value,40,40,90,Fake.angles[5]])
        slot=self.cards.status()['slots'][1]
        self.assertTrue(slot['complete']);self.assertFalse(slot['verified'])
        self.assertEqual(slot['grasp']['readings'][4]['angle'],89)
        restored=TeachingCards(self.demo).status()
        self.assertEqual(restored['slots'][1]['grasp']['angles'],[90,45,40,40,90,130])
        self.assertEqual(restored['slots'][2]['target']['frame_id'],two)
        result=self.cards.preview(self.fields(1,'target'));self.assertEqual(result['draft']['case'],'sample')
        self.assertTrue(self.cards.photo(slot['target']['frame_id']).startswith(b'\xff\xd8'))
        self.assertEqual(Fake.calls,[])

    def test_invalid_or_stale_requests_do_not_capture_or_move(self):
        with patch.object(self.teach,'capture',side_effect=AssertionError('capture')):
            for fields in [self.fields(True,'target'),self.fields(10,'target'),self.fields(0,'target'),
                           self.fields(1,'above',angles=[90,50,40,40,89,80]),self.fields(0,'reference',angles=[90,50,40,40,89,80]),
                           dict(self.fields(1,'target'),expected_cards_sha256='bad')]:
                with self.assertRaises(ValueError):self.cards.record(fields)
        self.assertEqual(Fake.calls,[])

    def test_reference_wrong_gripper_does_not_reset_setup(self):
        before=self.teach.path.read_bytes();self.image()
        with self.assertRaises(ValueError):self.capture(0,'reference',[90,50,40,40,90,100])
        self.assertEqual(self.teach.path.read_bytes(),before);self.assertFalse(self.cards.path.exists())

    def test_recipe_save_no_movement_stale_revision_and_photo_invalidation(self):
        self.target();self.image((200,160));self.capture(1,'above',[90,50,40,40,90,80])
        old=self.fields(1,'above',angles=[90,55,40,40,90,80],order=[6,1,2,3,4,5],motion_ms=1500)
        response=self.cards.angles(old)
        self.assertNotIn('above',response['status']['slots'][1])
        self.assertEqual(response['status']['slots'][1]['plans']['above']['order'],[6,1,2,3,4,5])
        with self.assertRaises(ValueError):self.cards.angles(old)
        self.assertTrue(list(self.cards.path.parent.glob('teaching_cards.before_*.json')))
        self.assertEqual(Fake.calls,[])

    def test_baseline_change_marks_old_cards_stale_and_center_requires_confirmation(self):
        self.target();point=self.cards.status()['slots'][1]['target']['pixel']
        for value in [dict(pixel=point,confirmed=False),dict(pixel=[100,100],confirmed=True),dict(pixel=[True,160],confirmed=True)]:
            with self.assertRaises(ValueError):self.cards.confirm(self.fields(1,'target',**value))
        self.image();self.capture(0,'reference',[90,50,40,40,90,80])
        self.assertTrue(self.cards.status()['slots'][1]['stale'])
        with self.assertRaises(ValueError):self.cards.preview(self.fields(1,'target'))
        self.assertEqual(Fake.calls,[])

    def test_path_traversal_and_file_lock_before_capture(self):
        for value in ['../config','f'*32+'/x',None,'g'*32]:
            with self.assertRaises(ValueError):self.cards.photo(value)
        lock=self.cards.path.with_suffix('.lock');lock.write_text('busy')
        with patch.object(self.teach,'capture',side_effect=AssertionError('capture')):
            with self.assertRaises(ValueError):self.capture(0,'reference',[90,50,40,40,90,80])
        lock.unlink();self.assertEqual(Fake.calls,[])

    def test_explicit_legacy_import_preserves_photos_without_inventing_sixth_target(self):
        Fake.angles=[90,50,40,40,89,80];self.image((200,160))
        target=self.teach.capture(dict(kind='target',expected_sha256=self.teach.sha()))
        frames={}
        for phase in ['above','grasp']:
            self.image((200,160))
            frames[phase+'_frame']=self.teach.capture(dict(kind=phase,angles=[90,50,40,40,90],expected_sha256=self.teach.sha()))['id']
        result=self.cards.import_frames(self.fields(3,'target',target_frame=target['id'],**frames))
        slot=result['slots'][3]
        self.assertEqual(slot['target']['frame_id'],target['id'])
        self.assertEqual(slot['above']['angles'],[90,50,40,40,90,None])
        self.assertFalse(slot['center_confirmed']);self.assertEqual(Fake.calls,[])
        self.assertTrue(any(f['id']==target['id'] for f in result['legacy_frames']))

    def test_grasp_records_without_camera_or_image_and_rejects_wrong_targets(self):
        self.target();Fake.angles=[90,45,40,40,89,130]
        with patch.object(self.demo.auto,'capture',side_effect=AssertionError('camera')):
            result=self.capture(1,'grasp',[90,45,40,40,90,130])
        record=result['status']['slots'][1]['grasp']
        self.assertFalse(record['has_image'])
        self.assertFalse((self.demo.auto.samples/(record['frame_id']+'.jpg')).exists())
        self.assertEqual(self.teach.evidence(record['frame_id'])['targets'],[90,45,40,40,90])
        before=self.cards.raw()
        with self.assertRaises(ValueError):self.capture(1,'grasp',[90,45,40,40,90,100])
        self.assertEqual(self.cards.raw(),before);self.assertEqual(Fake.calls,[])

    def test_pickup_draft_uses_card_orders_without_common_poses(self):
        cfg=self.teach.load();cfg.update(task_mode='pickup_only',common={},orders={})
        self.teach.path.write_text(json.dumps(cfg))
        self.target();point=self.cards.status()['slots'][1]['target']['pixel']
        self.cards.confirm(self.fields(1,'target',pixel=point,confirmed=True))
        for phase,gripper in [('above',80),('grasp',130)]:
            goals=[90,50,40,40,90,gripper]
            self.cards.angles(self.fields(1,phase,angles=goals,order=[1,5,4,3,2,6],motion_ms=1500))
            Fake.angles=goals[:];Fake.angles[4]=89
            self.image((200,160));self.capture(1,phase,goals)
        draft=self.cards.preview(self.fields(1,'target'))['draft']
        self.assertEqual(draft['plan']['task_mode'],'pickup_only')
        self.assertEqual(len(draft['plan']['steps']),12)
        self.assertEqual(draft['plan']['steps'][-1]['joints'],[[6,130]])
        self.assertEqual(Fake.calls,[])


if __name__=='__main__':unittest.main()
