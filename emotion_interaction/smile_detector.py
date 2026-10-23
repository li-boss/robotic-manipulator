"""Local smile evidence from official MediaPipe blendshapes, not emotion inference."""
import hashlib
import math
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"


def smile_evidence(face_count, blendshapes):
    """Require a single face and both smile coefficients. Score is NOT a probability."""
    empty = {"face_count": face_count, "smile_score": 0.0,
             "mouth_smile_left": None, "mouth_smile_right": None}
    if face_count != 1:
        return {**empty, "status": "NO_FACE" if face_count == 0 else "MULTIPLE_FACES"}
    left, right = blendshapes.get("mouthSmileLeft"), blendshapes.get("mouthSmileRight")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
           for v in (left, right)):
        return {**empty, "status": "INVALID_BLENDSHAPES"}
    return {"face_count": 1, "status": "FACE_DETECTED", "smile_score": min(left, right),
            "mouth_smile_left": left, "mouth_smile_right": right}


class SmileDetector:
    def __init__(self, progress=None):
        report = progress or (lambda message: None)
        report("正在加载笑脸定位组件……")
        if not MODEL_PATH.is_file():
            raise FileNotFoundError("Run: python -m integration_tools.download_smile_assets")
        if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("FACE_MODEL_HASH_MISMATCH")
        import mediapipe as mp
        self.mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=2, output_face_blendshapes=True)
        report("正在初始化笑脸模型……")
        self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        report("笑脸模型已就绪。")

    def detect(self, bgr):
        import cv2
        import numpy as np
        if not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
            raise ValueError("INVALID_BGR_FRAME")
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        result = self.landmarker.detect(image)
        count = len(result.face_landmarks)
        coefficients = ({c.category_name: float(c.score) for c in result.face_blendshapes[0]}
                        if count == 1 and result.face_blendshapes else {})
        evidence = smile_evidence(count, coefficients)
        if count == 1:
            points = result.face_landmarks[0]
            evidence["face_box"] = [min(p.x for p in points), min(p.y for p in points),
                                    max(p.x for p in points), max(p.y for p in points)]
        else:
            evidence["face_box"] = None
        return evidence

    def close(self):
        self.landmarker.close()
