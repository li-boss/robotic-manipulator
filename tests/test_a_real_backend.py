"""Risk-focused checks for real-backend coordinates, failures, and concurrency."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

from camera_service.camera import FrameError, FrameStore
from camera_service.service import VisionService
from waste_vision.detector import Detector, ModelError
from waste_vision.yolov8_backend import YoloV8nBackend

MAPPING='configs/a_category_mapping.json'

class RawStub:
    is_simulated=False
    box_coordinate_space='raw_camera'
    def __init__(self,rows):self.rows=rows
    def infer(self,frame):return self.rows

class RealBackendBoundaryTests(unittest.TestCase):
    def setUp(self):self.frame=FrameStore('test',max_age_ms=0).publish(b'fixture',640,480)

    def test_raw_model_box_keeps_full_image_boundary_without_second_restore(self):
        row={'label':'block_blue','confidence':.91,'bbox':[0,2,640,480]}
        result=Detector(RawStub([row]),MAPPING).detect(self.frame)
        target=result['targets'][0]
        self.assertEqual(target['bbox'],[0.0,2.0,640.0,480.0])
        self.assertEqual((target['center_x'],target['center_y']),(320.0,241.0))
        self.assertFalse(result['simulated'])
        self.assertIsNone(target['category'])
        self.assertEqual(target['target_status'],'unmapped')

    def test_invalid_raw_coordinate_is_error_not_clipped(self):
        bad={'label':'block_blue','confidence':.9,'bbox':[0,0,641,480]}
        with self.assertRaises(ModelError) as caught:
            Detector(RawStub([bad]),MAPPING).detect(self.frame)
        self.assertEqual(caught.exception.code,'INFERENCE_FAILED')

    def test_no_target_and_low_confidence_are_distinct(self):
        empty=Detector(RawStub([]),MAPPING).detect(self.frame)
        self.assertEqual(empty['outcome'],'no_target')
        self.assertFalse(empty['simulated'])
        low=Detector(RawStub([{'label':'block_red','confidence':.3,'bbox':[1,1,20,20]}]),MAPPING).detect(self.frame)
        self.assertEqual(low['targets'][0]['target_status'],'low_confidence')

    def test_missing_real_weights_fails_at_load(self):
        with self.assertRaises(ModelError) as caught:
            YoloV8nBackend('data/a_blocks/never_exists_model.pt')
        self.assertEqual(caught.exception.code,'MODEL_UNAVAILABLE')

    def test_concurrent_detection_is_serialized_but_frame_reads_continue(self):
        class Slow(RawStub):
            def __init__(self):
                self.guard=Lock();self.active=0;self.maximum=0
            def infer(self,frame):
                with self.guard:
                    self.active+=1;self.maximum=max(self.maximum,self.active)
                sleep(.04)
                with self.guard:self.active-=1
                return []
        store=FrameStore('test',max_age_ms=0);frame=store.publish(b'fixture',640,480)
        backend=Slow();service=VisionService(store,Detector(backend,MAPPING))
        with ThreadPoolExecutor(max_workers=3) as pool:
            a=pool.submit(service.detect,'a',frame.frame_id)
            b=pool.submit(service.detect,'b',frame.frame_id)
            read=pool.submit(store.get,frame.frame_id)
            self.assertEqual(read.result(),frame)
            self.assertEqual(a.result()['outcome'],'no_target')
            self.assertEqual(b.result()['outcome'],'no_target')
        self.assertEqual(backend.maximum,1)


    def test_result_expiring_during_inference_is_rejected_and_cached_as_error(self):
        class Slow(RawStub):
            def infer(self,frame):
                sleep(.05)
                return [{'label':'block_blue','confidence':.9,'bbox':[1,1,20,20]}]
        store=FrameStore('live',max_age_ms=20);store.publish(b'fixture',640,480)
        service=VisionService(store,Detector(Slow([]),MAPPING))
        with self.assertRaises(FrameError) as caught:
            service.detect('expiry','latest')
        self.assertEqual(caught.exception.code,'FRAME_EXPIRED')
        details=caught.exception.details
        self.assertGreater(details['frame_age_at_finish_ms'],details['max_age_ms'])
        self.assertLessEqual(details['captured_at'],details['inference_started_at'])
        self.assertLessEqual(details['inference_started_at'],details['inference_finished_at'])
        with self.assertRaises(FrameError) as repeated:
            service.detect('expiry','latest')
        self.assertIs(repeated.exception,caught.exception)

    def test_disconnect_during_inference_is_not_returned_as_target(self):
        store=FrameStore('live',max_age_ms=5000);store.publish(b'fixture',640,480)
        class Disconnect(RawStub):
            def infer(self,frame):
                store.mark_unavailable()
                return [{'label':'block_blue','confidence':.9,'bbox':[1,1,20,20]}]
        service=VisionService(store,Detector(Disconnect([]),MAPPING))
        with self.assertRaises(FrameError) as caught:
            service.detect('lost','latest')
        self.assertEqual(caught.exception.code,'CAMERA_UNAVAILABLE')

    def test_readiness_requires_backend_and_fresh_frame(self):
        store=FrameStore('live',max_age_ms=50)
        backend=RawStub([]);backend.ready=False
        service=VisionService(store,Detector(backend,MAPPING))
        code,body=service.readiness()
        self.assertEqual(code,503);self.assertFalse(body['camera_ready'])
        store.publish(b'fixture',640,480)
        code,body=service.readiness()
        self.assertEqual(code,503);self.assertFalse(body['model_ready'])
        backend.ready=True
        code,body=service.readiness()
        self.assertEqual(code,200);self.assertEqual(body['status'],'ready')
        store.mark_unavailable()
        code,body=service.readiness()
        self.assertEqual(code,503);self.assertFalse(body['camera_ready'])


    def test_warmup_failure_is_explicit_and_never_ready(self):
        import numpy as np
        class Broken:
            def predict(self,**kwargs):raise RuntimeError('predictor could not initialize')
        backend=object.__new__(YoloV8nBackend)
        backend._np=np;backend._model=Broken();backend._model_lock=Lock()
        backend.imgsz=640;backend.confidence=.25;backend.nms_iou=.5
        backend.device='cpu';backend.ready=False
        with self.assertRaises(ModelError) as caught:
            backend.warmup()
        self.assertEqual(caught.exception.code,'MODEL_WARMUP_FAILED')
        self.assertFalse(backend.ready)

if __name__=='__main__':unittest.main()
