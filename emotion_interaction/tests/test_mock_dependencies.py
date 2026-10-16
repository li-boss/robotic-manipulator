import tempfile
import unittest
from pathlib import Path
from integration_tools.mocks.fake_camera import FakeCamera
from integration_tools.mocks.fake_detect import FakeDetect
from integration_tools.mocks.fake_event_sink import FakeEventSink


class MockDependencyTests(unittest.TestCase):
    def test_frame_metadata_payload_and_restart_ids(self):
        camera=FakeCamera(); frame=camera.read(1000)
        self.assertNotIn("payload",frame.metadata())
        self.assertEqual(len(frame.payload),frame.width*frame.height*3)
        self.assertNotEqual(frame.frame_id,camera.read(1001).frame_id)
        self.assertNotEqual(frame.frame_id,FakeCamera().read(1000).frame_id)

    def test_down_and_expired(self):
        camera=FakeCamera()
        self.assertEqual(camera.read(1000,"camera_down")["code"],"CAMERA_UNAVAILABLE")
        frame=camera.read(1000,"expired")
        result=FakeDetect().detect({"request_id":"r1","frame_id":"latest"},frame,1000)
        self.assertEqual(result["code"],"FRAME_EXPIRED")

    def test_normal_low_empty(self):
        frame=FakeCamera().read(1000)
        detector=FakeDetect()
        request={"request_id":"r1","frame_id":frame.frame_id}
        for scenario in ("normal","low_confidence","no_target"):
            result=detector.detect(request,frame,1000,scenario)
            self.assertEqual(result["frame_id"],frame.frame_id)
            if scenario=="no_target": self.assertEqual(result["targets"],[])
            elif scenario=="low_confidence": self.assertLess(result["targets"][0]["confidence"],.5)
            else: self.assertGreaterEqual(result["targets"][0]["confidence"],.5)

    def test_sink_outage_restart_duplicate_and_conflict(self):
        event={"event_id":"e1","task_id":"t1","seq":1,"occurred_at":1000,"event_type":"STARTED","device_state":"BUSY"}
        temporary_root=Path(__file__).resolve().parents[2]/"tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path=Path(directory)/"sink.sqlite3"
            sink=FakeEventSink(path)
            try:
                sink.online=False
                self.assertEqual(sink.receive(event)[0],503)
                sink.online=True
                self.assertEqual(sink.receive(event),(202,{"accepted":True,"duplicate":False}))
            finally: sink.close()
            sink=FakeEventSink(path)
            try:
                self.assertEqual(sink.receive(event),(202,{"accepted":True,"duplicate":True}))
                self.assertEqual(sink.receive({**event,"seq":2})[0],409)
            finally: sink.close()


if __name__ == "__main__": unittest.main()
