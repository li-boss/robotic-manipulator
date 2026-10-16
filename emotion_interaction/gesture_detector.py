"""Local gesture adapter; no robot transport and no changes to contracts/."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "models" / "HandGestureRecognition-main"


class GestureClassifier:
    def __init__(self):
        import torch
        self.torch = torch
        self.manifest = json.loads((ROOT / "gesture_labels.json").read_text(encoding="utf-8"))
        path = UPSTREAM / "best_hand_gesture_classifier.pt"
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.manifest["model_sha256"]:
            raise ValueError("MODEL_HASH_MISMATCH: label table is bound to the downloaded weight")
        self.model = torch.jit.load(str(path), map_location="cpu").eval()
        with torch.inference_mode():
            shape = tuple(self.model(torch.zeros(1, 1, 128, 128)).shape)
        if shape != (1, len(self.manifest["labels"])):
            raise ValueError(f"MODEL_LABEL_COUNT_MISMATCH: {shape}")

    def predict_wireframe(self, image):
        """Input is a black-background hand skeleton, NOT a raw camera crop."""
        import cv2
        import numpy as np
        if image is None or image.size == 0:
            raise ValueError("EMPTY_IMAGE")
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Match upstream preprocessing: grayscale, 128x128, [-1, 1].
        image = cv2.resize(image, (128, 128)).astype(np.float32) / 127.5 - 1.0
        tensor = self.torch.from_numpy(image).unsqueeze(0).unsqueeze(0)
        with self.torch.inference_mode():
            probabilities = self.model(tensor).softmax(dim=1)[0]
            confidence, index = probabilities.max(dim=0)
        raw = self.manifest["labels"][index.item()]
        return {"raw_label": raw, "gesture": self.manifest["project_labels"].get(raw),
                "confidence": confidence.item(), "class_index": index.item()}


class GestureDetector:
    def __init__(self, progress=None):
        report = progress or (lambda message: None)
        report("正在加载手部定位组件……")
        import mediapipe as mp
        self.mp = mp
        report("正在加载手势分类模型……")
        self.classifier = GestureClassifier()
        report("正在初始化手部定位模型……")
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(ROOT / "models" / "hand_landmarker.task")),
            running_mode=mp.tasks.vision.RunningMode.IMAGE, num_hands=2)
        self.landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        report("识别模型已就绪。")

    def detect(self, bgr):
        import cv2
        import numpy as np
        result = self.landmarker.detect(self.mp.Image(
            image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        count = len(result.hand_landmarks)
        if count != 1:
            return {"gesture": None, "raw_label": "no_hand" if count == 0 else "multiple_hands",
                    "confidence": 0.0, "hand_count": count}
        h, w = bgr.shape[:2]
        points = [(int(p.x * w), int(p.y * h)) for p in result.hand_landmarks[0]]
        xs, ys = zip(*points)
        # Same square ROI, 50 px padding, and skeleton rendering as upstream demo.
        size = max(max(xs) - min(xs), max(ys) - min(ys)) + 100
        x = max(0, min((min(xs) + max(xs)) // 2 - size // 2, w - size))
        y = max(0, min((min(ys) + max(ys)) // 2 - size // 2, h - size))
        size = min(size, w - x, h - y)
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        local = [(px - x, py - y) for px, py in points]
        thickness = max(1, int(size / 100 * 3))
        inside = lambda p: 0 <= p[0] < size and 0 <= p[1] < size
        return self._classify_canvas(canvas, local, inside, thickness)

    def _classify_canvas(self, canvas, local, inside, thickness):
        import cv2
        connections = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
                       (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),
                       (15,16),(13,17),(0,17),(17,18),(18,19),(19,20)]
        for a, b in connections:
            if inside(local[a]) and inside(local[b]):
                cv2.line(canvas, local[a], local[b], (255,255,255), thickness)
        for p in local:
            if inside(p):
                cv2.circle(canvas, p, thickness, (255,255,255), -1)
        prediction = self.classifier.predict_wireframe(cv2.resize(canvas, (200, 200)))
        return {**prediction, "hand_count": 1}

    def close(self):
        self.landmarker.close()
