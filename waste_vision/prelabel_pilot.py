"""Use an actual YOLOv8n weight for a small, explicitly unreviewed annotation pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

CLASSES = ("block_blue", "block_green", "block_red", "block_yellow")
DEFAULT_IMAGES = ("200.jpg", "222.jpg", "232.jpg", "238.jpg", "264.jpg",
                  "272.jpg", "296.jpg", "334.jpg", "387.jpg", "430.jpg")
COLORS = ("#1687ee", "#238a39", "#e33831", "#d5a900")


def xyxy_to_yolo(cls: int, box: list[float], width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    if cls not in range(4) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"invalid original-image box: {cls} {box} at {width}x{height}")
    return f"{cls} {(x1+x2)/(2*width):.6f} {(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}"


def run(args: argparse.Namespace) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    from ultralytics import YOLO
    import torch
    import ultralytics

    source, output, weights = args.source.resolve(), args.output.resolve(), args.weights.resolve()
    names = tuple(args.images)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    if len(set(names)) != len(names) or not names:
        raise ValueError("image list must contain unique names")
    for name in names:
        if Path(name).name != name or not (source / name).is_file():
            raise FileNotFoundError(source / name)
    if not weights.is_file():
        raise FileNotFoundError(weights)
    model = YOLO(str(weights))
    actual = tuple(model.names[i] for i in sorted(model.names))
    if actual != CLASSES:
        raise ValueError(f"weight class order {actual} != {CLASSES}")
    output.mkdir(parents=True)
    for sub in ("images", "suggestions", "previews", "reviewed_labels"):
        (output / sub).mkdir()
    (output / "suggestions" / "UNREVIEWED_MODEL_SUGGESTIONS.json").write_text(
        json.dumps({"status": "pending_human_review", "not_training_labels": True}), encoding="utf-8")
    records = []
    thumbnails = []
    for name in names:
        origin = source / name
        shutil.copy2(origin, output / "images" / name)
        if hashlib.sha256(origin.read_bytes()).digest() != hashlib.sha256((output / "images" / name).read_bytes()).digest():
            raise IOError(f"image copy mismatch: {name}")
        with Image.open(origin) as raw:
            raw.load()
            canvas = raw.convert("RGB")
        width, height = canvas.size
        result = model.predict(source=str(origin), conf=args.conf, iou=args.iou,
                               imgsz=args.imgsz, device="cpu", verbose=False)[0]
        if tuple(result.orig_shape) != (height, width):
            raise ValueError(f"model orig_shape mismatch: {name}")
        boxes, lines = [], []
        draw = ImageDraw.Draw(canvas)
        for det in result.boxes:
            cls = int(det.cls.item())
            conf = float(det.conf.item())
            box = [float(v) for v in det.xyxy[0].tolist()]
            lines.append(xyxy_to_yolo(cls, box, width, height))
            boxes.append({"class_id": cls, "raw_label": CLASSES[cls], "confidence": conf,
                          "bbox_xyxy_raw_camera": box,
                          "center_x": (box[0]+box[2])/2, "center_y": (box[1]+box[3])/2})
            draw.rectangle(box, outline=COLORS[cls], width=3)
            draw.text((box[0]+2, max(0, box[1]-14)), f"{CLASSES[cls]} {conf:.2f}", fill=COLORS[cls])
        (output / "suggestions" / f"{Path(name).stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        draw.rectangle((0, 0, width-1, 22), fill="white")
        draw.text((4, 4), f"{name}  MODEL SUGGESTION / PENDING HUMAN REVIEW", fill="black")
        canvas.save(output / "previews" / f"{Path(name).stem}.png")
        small = canvas.resize((480, 360))
        thumbnails.append(small)
        records.append({"filename": name, "source": str(origin), "sha256": hashlib.sha256(origin.read_bytes()).hexdigest(),
                        "width": width, "height": height, "review_status": "pending",
                        "suggestion_count": len(boxes), "boxes": boxes,
                        "model_stage_ms": result.speed})
    sheet = Image.new("RGB", (960, 360 * ((len(thumbnails)+1)//2)), "white")
    for i, im in enumerate(thumbnails):
        sheet.paste(im, ((i % 2) * 480, (i // 2) * 360))
    sheet.save(output / "pilot_contact_sheet.png")
    manifest = {"purpose": "model preannotations; ALL pending human review; NOT training labels",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_dir": str(output / "images"),
                "suggestions_dir": str(output / "suggestions"),
                "reviewed_labels_dir": str(output / "reviewed_labels"),
                "classes": CLASSES, "weights": str(weights),
                "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                "torch": torch.__version__, "ultralytics": ultralytics.__version__,
                "device": "cpu", "conf": args.conf, "iou": args.iou, "imgsz": args.imgsz,
                "images": records}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"images": len(records), "total_suggestions": sum(r["suggestion_count"] for r in records),
            "by_image": {r["filename"]: r["suggestion_count"] for r in records},
            "output": str(output), "status": "pending_human_review"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/a_blocks/annotation_work"))
    parser.add_argument("--weights", type=Path, default=Path("data/a_blocks/runs/yolov8n_smoke_cpu_20261022_01/weights/best.pt"))
    parser.add_argument("--output", type=Path, default=Path("data/a_blocks/prelabel_pilot10"))
    parser.add_argument("--images", nargs="+", default=DEFAULT_IMAGES)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=640)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments), ensure_ascii=False, indent=2))
