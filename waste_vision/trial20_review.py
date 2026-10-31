"""Check only the 20 trial labels and render human-review overlays when present."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from waste_vision.label_check import check
from waste_vision.trial20 import CLASSES


def review(trial: Path, classes: Path, full_images: Path, output: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    manifest = json.loads((trial / "manifest.json").read_text(encoding="utf-8"))
    names = [row["filename"] for row in manifest["images"]]
    if len(names) != 20 or tuple(manifest["classes"]) != CLASSES:
        raise ValueError("trial manifest or class order has changed")
    result = check(trial / "images", trial / "labels", classes)
    pending = json.loads((trial / "pending_review.json").read_text(encoding="utf-8")) if (trial / "pending_review.json").exists() else []
    empty_labels = [name for name in names if (trial / "labels" / f"{Path(name).stem}.txt").exists()
                    and not (trial / "labels" / f"{Path(name).stem}.txt").read_text(encoding="utf-8").strip()]
    warnings = []
    output.mkdir(parents=True, exist_ok=True)
    overlays = []
    colors = ("#2196f3", "#26882f", "#e43b35", "#bd9a00")
    for name in names:
        source = trial / "images" / name
        label = trial / "labels" / f"{Path(name).stem}.txt"
        if not label.exists():
            continue
        with Image.open(source) as image_file:
            image = image_file.convert("RGB")
        width, height = image.size
        draw = ImageDraw.Draw(image)
        for number, line in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
            try:
                class_id, cx, cy, bw, bh = line.split()
                class_id = int(class_id)
                cx, cy, bw, bh = map(float, (cx, cy, bw, bh))
                if class_id not in range(4) or not (0 < bw <= 1 and 0 < bh <= 1 and 0 <= cx-bw/2 < cx+bw/2 <= 1 and 0 <= cy-bh/2 < cy+bh/2 <= 1):
                    raise ValueError("invalid class or box")
            except (ValueError, TypeError) as exc:
                warnings.append({"file": name, "line": number, "warning": f"cannot draw invalid label: {exc}"})
                continue
            x1, y1, x2, y2 = (cx-bw/2)*width, (cy-bh/2)*height, (cx+bw/2)*width, (cy+bh/2)*height
            draw.rectangle((x1, y1, x2, y2), outline=colors[class_id], width=3)
            text = f"{class_id} {CLASSES[class_id]}"
            text_box = draw.textbbox((x1, y1), text)
            top = max(0, y1 - (text_box[3] - text_box[1]) - 5)
            draw.rectangle((x1, top, x1 + text_box[2] - text_box[0] + 5, y1), fill=colors[class_id])
            draw.text((x1+2, top), text, fill="white")
            if bw * bh > 0.4:
                warnings.append({"file": name, "line": number, "warning": "box covers over 40% of image; visually review"})
        draw.text((6, 6), name, fill="black", stroke_width=2, stroke_fill="white")
        destination = output / name
        image.save(destination)
        overlays.append(destination)
    from waste_vision.dataset_prep import contact_sheet
    if overlays:
        contact_sheet(overlays, output / "已标注预览联系表.png")
    full_count = len(list(full_images.glob("*.jpg")))
    outside_trial = full_count - len(names)
    report = {
        "trial_images": len(names), "trial_labeled": result["labeled_images"],
        "trial_missing": result["missing_labels"], "trial_problems": result["problems"],
        "trial_pending_review": pending, "trial_empty_labels_invalid_for_this_positive_trial": empty_labels,
        "trial_extra_labels": result["extra_label_files"],
        "trial_ready_for_manual_visual_review": result["ready_for_training"] and not pending and not empty_labels,
        "outside_trial_images": outside_trial,
        "outside_trial_status": "not evaluated as trial labels; do not treat as empty scenes",
        "overlays_created": len(overlays), "visual_warnings": warnings,
        "visual_review_required": "Check each overlay for missing objects, wrong class, shifted or oversized boxes. Format validation is insufficient.",
    }
    (output / "试标检查结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, default=Path("data/a_blocks/trial20"))
    parser.add_argument("--classes", type=Path, default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--full-images", type=Path, default=Path("data/a_blocks/annotation_work"))
    parser.add_argument("--output", type=Path, default=Path("data/a_blocks/trial20/review"))
    args = parser.parse_args()
    print(json.dumps(review(args.trial, args.classes, args.full_images, args.output), ensure_ascii=False, indent=2))
