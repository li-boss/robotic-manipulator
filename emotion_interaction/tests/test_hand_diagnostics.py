import unittest
from types import SimpleNamespace
from emotion_interaction.gesture_detector import GestureDetector
from emotion_interaction.preview_overlay import hand_status


class HandDiagnosticsTests(unittest.TestCase):
    def test_status_distinguishes_detection_from_classification(self):
        cases=[({"hand_count":0},"NO_HAND"),({"hand_count":2},"MULTIPLE_HANDS"),
               ({"hand_count":1,"gesture":None,"confidence":.99},"NON_TARGET_GESTURE"),
               ({"hand_count":1,"gesture":"like","confidence":.7},"LOW_CONFIDENCE"),
               ({"hand_count":1,"gesture":"like","confidence":.9},"DETECTED")]
        for prediction,expected in cases:
            self.assertEqual(hand_status(prediction),expected)

    def make_detector(self, hands):
        import numpy as np
        detector=GestureDetector.__new__(GestureDetector)
        detector.mp=SimpleNamespace(ImageFormat=SimpleNamespace(SRGB="rgb"),Image=lambda **kwargs:kwargs["data"])
        seen=[]
        def detect(image):
            seen.append(image.shape)
            return SimpleNamespace(hand_landmarks=hands)
        detector.landmarker=SimpleNamespace(detect=detect)
        detector._classify_canvas=lambda *args:{"gesture":"like","raw_label":"thumb_up","confidence":.9,"hand_count":1}
        return detector,seen,np.zeros((480,640,3),dtype=np.uint8)

    def test_hand_outside_face_region_gets_full_frame_and_box(self):
        # Hand at right side of full image; no face crop is passed to the detector.
        hand=[SimpleNamespace(x=.8+i*.004,y=.6+i*.004) for i in range(21)]
        detector,seen,frame=self.make_detector([hand])
        result=detector.detect(frame)
        self.assertEqual(seen,[(480,640,3)])
        self.assertEqual(result["hand_count"],1)
        self.assertGreaterEqual(result["hand_boxes"][0][0],.8)
        self.assertEqual(result["gesture"],"like")

    def test_multiple_hands_keep_boxes_but_no_gesture(self):
        hand=[SimpleNamespace(x=.2+i*.003,y=.3+i*.003) for i in range(21)]
        detector,_,frame=self.make_detector([hand,hand])
        result=detector.detect(frame)
        self.assertEqual(len(result["hand_boxes"]),2)
        self.assertIsNone(result["gesture"])


if __name__ == "__main__": unittest.main()
