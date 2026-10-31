
"""Dataset inventory and label validation; not a substitute for mAP testing."""
import argparse
import json
from collections import Counter
from pathlib import Path

from camera_service.frame_source import image_size


def audit(images: Path, labels: Path | None = None, class_names: list[str] | None = None) -> dict:
    paths = sorted(p for p in images.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    summary = {"images": len(paths), "valid_images": 0, "invalid_images": [],
               "dimensions": {}, "labels_present": 0, "missing_labels": [],
               "invalid_labels": [], "class_counts": {}}
    classes = Counter()
    for image in paths:
        try:
            width, height = image_size(image.read_bytes())
            if width < 1 or height < 1:
                raise ValueError("invalid dimensions")
            summary["valid_images"] += 1
            key = f"{width}x{height}"
            summary["dimensions"][key] = summary["dimensions"].get(key, 0) + 1
        except (ValueError, OSError) as exc:
            summary["invalid_images"].append({"file": str(image), "reason": str(exc)})
        if labels is None:
            continue
        label_path = labels / image.relative_to(images).with_suffix(".txt")
        if not label_path.is_file():
            summary["missing_labels"].append(str(image))
            continue
        summary["labels_present"] += 1
        for number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                values = line.split()
                if len(values) != 5:
                    raise ValueError("expected YOLO class cx cy w h")
                class_id = int(values[0])
                box = list(map(float, values[1:]))
                if class_id < 0 or (class_names is not None and class_id >= len(class_names)):
                    raise ValueError("class id out of range")
                if not all(0 <= x <= 1 for x in box) or box[2] <= 0 or box[3] <= 0:
                    raise ValueError("invalid normalized box")
                classes[str(class_id)] += 1
            except ValueError as exc:
                summary["invalid_labels"].append({"file": str(label_path), "line": number, "reason": str(exc)})
    summary["class_counts"] = dict(classes)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--classes", nargs="*")
    args = parser.parse_args()
    print(json.dumps(audit(args.images, args.labels, args.classes), ensure_ascii=False, indent=2))
