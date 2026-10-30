"""No camera used: verify model API, blank-frame behavior and optional local sample."""
import argparse
import importlib.util
import json
from pathlib import Path


def main():
    import cv2
    import numpy as np
    import mediapipe as mp
    from emotion_interaction.smile_detector import SmileDetector, MODEL_PATH, MODEL_SHA256
    parser=argparse.ArgumentParser()
    parser.add_argument("--image",type=Path,help="Optional face image for local smoke testing")
    args=parser.parse_args()
    detector=SmileDetector()
    try:
        blank=detector.detect(np.zeros((480,640,3),dtype=np.uint8))
        assert blank["face_count"]==0 and blank["smile_score"]==0
        sample=None
        if args.image:
            image=cv2.imread(str(args.image))
            if image is None: raise ValueError("IMAGE_UNREADABLE")
            sample=detector.detect(image)
            assert sample["face_count"]==1 and sample["status"]=="FACE_DETECTED", sample
        report={"mediapipe":mp.__version__,"opencv":cv2.__version__,
                "tensorflow_visible":importlib.util.find_spec("tensorflow") is not None,
                "model_path":str(MODEL_PATH),"model_sha256":MODEL_SHA256,
                "blank_frame":blank,"sample_result":sample,
                "limitation":"Smile score is a blendshape coefficient, not a calibrated probability. No accuracy or hardware success claimed."}
        path=Path("logs/smile_model_check.json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2))
        print(f"Report: {path.resolve()}")
    finally:
        detector.close()


if __name__ == "__main__": main()
