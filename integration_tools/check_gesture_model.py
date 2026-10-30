"""Inspect model, infer packaged skeleton examples, and smoke-test Hand Landmarker."""
import json
from collections import Counter
from pathlib import Path


def main():
    import cv2
    import numpy as np
    from emotion_interaction.gesture_detector import GestureClassifier, GestureDetector, UPSTREAM
    classifier = GestureClassifier()
    rows = []
    for path in sorted((UPSTREAM / "images" / "gestures").glob("*.jpg")):
        expected = path.stem.rsplit("_", 1)[0]
        result = classifier.predict_wireframe(cv2.imread(str(path)))
        rows.append({"sample": path.name, "expected": expected, **result,
                     "correct": result["raw_label"] == expected})
    detector = GestureDetector()
    try:
        blank = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
    finally:
        detector.close()
    report = {"class_count": len(classifier.manifest["labels"]),
              "label_order_source": classifier.manifest["label_evidence"],
              "example_count": len(rows), "correct_examples": sum(r["correct"] for r in rows),
              "per_class_errors": dict(Counter(r["expected"] for r in rows if not r["correct"])),
              "blank_frame": blank, "samples": rows,
              "limitation": "Upstream examples are not independent test data. No camera accuracy or hardware validation claimed."}
    output = Path("logs/gesture_model_check.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "samples"}, ensure_ascii=False, indent=2))
    print(f"Report: {output.resolve()}")
    if blank["hand_count"] != 0 or len(rows) != 48 or not all(row["correct"] for row in rows):
        raise SystemExit("Model smoke test failed")


if __name__ == "__main__":
    main()
