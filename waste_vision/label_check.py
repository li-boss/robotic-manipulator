"""Validate human-reviewed YOLO detection labels before any training."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from camera_service.frame_source import image_size


def check(images_dir: Path, labels_dir: Path, classes_path: Path) -> dict:
    classes = [line.strip() for line in classes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not classes or len(set(classes)) != len(classes):
        raise ValueError("confirmed classes file must contain unique names in fixed order")
    images = sorted(p for p in images_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    problems = []
    marker = labels_dir / "UNREVIEWED_MODEL_SUGGESTIONS.json"
    if marker.exists():
        problems.append({"file": str(marker), "error": "model suggestions are pending human review and cannot be training labels"})
    missing = []
    counts = Counter()
    expected_labels = set()
    for image in images:
        relative = image.relative_to(images_dir)
        label = labels_dir / relative.with_suffix(".txt")
        expected_labels.add(label.resolve())
        try:
            width, height = image_size(image.read_bytes())
            if width <= 0 or height <= 0:
                raise ValueError("invalid image dimensions")
        except (OSError, ValueError) as exc:
            problems.append({"file": str(image), "error": f"invalid image: {exc}"})
        if not label.is_file():
            missing.append(str(relative))
            continue
        for number, row in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
            try:
                items = row.split()
                if len(items) != 5:
                    raise ValueError("expected class_id cx cy w h")
                index = int(items[0])
                cx, cy, bw, bh = (float(value) for value in items[1:])
                if index < 0 or index >= len(classes):
                    raise ValueError("class index outside confirmed class order")
                if not (0 < bw <= 1 and 0 < bh <= 1 and
                        0 <= cx - bw / 2 < cx + bw / 2 <= 1 and
                        0 <= cy - bh / 2 < cy + bh / 2 <= 1):
                    raise ValueError("box outside normalized image or zero-sized")
                counts[classes[index]] += 1
            except ValueError as exc:
                problems.append({"file": str(label), "line": number, "error": str(exc)})
    extra = [str(p) for p in labels_dir.rglob("*.txt") if p.resolve() not in expected_labels and p.name != "classes.txt"]
    return {"images": len(images), "class_order": classes,
            "labeled_images": len(images) - len(missing),
            "missing_labels": missing, "class_box_counts": dict(counts),
            "extra_label_files": extra, "problems": problems,
            "ready_for_training": bool(images) and not (missing or extra or problems)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--classes", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.images, args.labels, args.classes), ensure_ascii=False, indent=2))
