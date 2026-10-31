"""YOLOv8n training-pipeline smoke run; its four val images are not an independent test set."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from waste_vision.label_check import check
from waste_vision.trial20 import CLASSES


def validate_flow(root: Path, classes_file: Path) -> dict:
    split = json.loads((root / "split.json").read_text(encoding="utf-8"))
    classes = tuple(line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip())
    if classes != CLASSES or tuple(split["classes"]) != CLASSES or len(split["files"]) != 20:
        raise ValueError("class order or 20-image flow split changed")
    if split["train_count"] != 16 or split["val_count"] != 4:
        raise ValueError("unexpected flow split size")
    checks = {}
    for part, expected in (("train", 16), ("val", 4)):
        result = check(root / "images" / part, root / "labels" / part, classes_file)
        if result["images"] != expected or not result["ready_for_training"]:
            raise ValueError(f"invalid {part} images or labels: {result}")
        checks[part] = result["class_box_counts"]
    for record in split["files"]:
        part, name = record["split"], record["image"]
        image = root / "images" / part / name
        label = root / "labels" / part / f"{Path(name).stem}.txt"
        if hashlib.sha256(image.read_bytes()).hexdigest() != record["image_sha256"]:
            raise ValueError(f"changed image: {name}")
        if hashlib.sha256(label.read_bytes()).hexdigest() != record["label_sha256"]:
            raise ValueError(f"changed label: {name}")
    return checks


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", type=Path, default=Path("data/a_blocks/trial20_flow"))
    parser.add_argument("--classes", type=Path, default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", choices=("0", "cpu"), default="0",
                        help="Use 0 for CUDA GPU, or explicitly choose cpu for a pipeline-only run")
    parser.add_argument("--output", type=Path, default=Path("data/a_blocks/runs/yolov8n_smoke_01"))
    args = parser.parse_args()
    if args.epochs not in range(1, 6) or args.batch < 1:
        parser.error("smoke run requires 1-5 epochs and positive batch")
    checks = validate_flow(args.flow, args.classes)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite run: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str((Path("data/a_blocks/ultralytics_config")).resolve()))
    import torch
    from ultralytics import YOLO, __version__ as ultralytics_version
    if args.device == "0" and not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU unavailable; select --device cpu explicitly for a pipeline-only run")
    started = time.perf_counter()
    info = {"purpose": "training pipeline smoke run; NOT model adequacy evidence",
            "started_at": now(), "flow": str(args.flow.resolve()), "class_order": CLASSES,
            "class_box_counts": checks, "python": sys.version,
            "torch": torch.__version__, "ultralytics": ultralytics_version,
            "cuda_runtime": torch.version.cuda, "device": args.device,
            "hardware": torch.cuda.get_device_name(0) if args.device == "0" else "CPU (pipeline smoke only)",
            "epochs": args.epochs, "batch": args.batch, "imgsz": 640,
            "hsv_h": 0.0, "hsv_s": 0.0, "hsv_v": 0.0,
            "validation_warning": "4 same-session val images; no accuracy or generalization claim"}
    args.output.mkdir()
    report_path = args.output / "flow_result.json"
    try:
        model = YOLO("yolov8n.pt")
        trained = model.train(data=str((args.flow / "data.yaml").resolve()),
                              epochs=args.epochs, batch=args.batch, imgsz=640,
                              device=args.device, workers=0, seed=42, deterministic=True,
                              hsv_h=0.0, hsv_s=0.0, hsv_v=0.0,
                              project=str(args.output.parent.resolve()),
                              name=args.output.name, exist_ok=True,
                              plots=True, save=True, cache=False)
        weights = args.output / "weights" / "best.pt"
        if not weights.is_file():
            raise FileNotFoundError(f"training returned without best.pt: {weights}")
        loaded = YOLO(str(weights))
        names = tuple(loaded.names[i] for i in range(4))
        if names != CLASSES:
            raise ValueError(f"trained model classes differ: {names}")
        image = args.flow / "images" / "val" / "193.jpg"
        if args.device == "0":
            torch.cuda.synchronize()
        prediction_started = time.perf_counter()
        results = loaded.predict(source=str(image), imgsz=640, conf=0.01,
                                 iou=0.5, device=args.device, verbose=False)
        if args.device == "0":
            torch.cuda.synchronize()
        prediction_ms = (time.perf_counter() - prediction_started) * 1000
        if len(results) != 1:
            raise RuntimeError(f"expected one prediction result, got {len(results)}")
        result = results[0]
        height, width = result.orig_shape
        boxes = []
        for row in result.boxes:
            x1, y1, x2, y2 = (float(x) for x in row.xyxy[0].tolist())
            class_id = int(row.cls[0])
            if not (0 <= class_id < 4 and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError(f"invalid model output coordinates/class: {class_id}, {(x1,y1,x2,y2)}")
            boxes.append({"raw_label": CLASSES[class_id], "confidence": float(row.conf[0]),
                          "bbox_xyxy_raw_camera": [x1, y1, x2, y2],
                          "center_x": (x1+x2)/2, "center_y": (y1+y2)/2})
        result.save(filename=str(args.output / "193_prediction.jpg"))
        info.update({"status": "flow_passed", "completed_at": now(),
                     "elapsed_s": time.perf_counter() - started,
                     "best_pt": str(weights.resolve()),
                     "best_pt_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                     "prediction_image": str(image.resolve()),
                     "prediction_orig_shape": [height, width],
                     "prediction_coordinate_space": "raw_camera original JPEG pixels",
                     "prediction_boxes": boxes, "prediction_request_ms": prediction_ms,
                     "model_stage_ms": result.speed,
                     "note": "Zero boxes is a valid real model output; visual usefulness is not established by this smoke run."})
    except Exception as exc:
        info.update({"status": "flow_failed", "completed_at": now(),
                     "error_type": type(exc).__name__, "error": str(exc)})
        report_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    report_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": info["status"], "weights": info["best_pt"],
                      "real_prediction_boxes": len(boxes), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
