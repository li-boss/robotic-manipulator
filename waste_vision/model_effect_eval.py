"""Audit an independent test split, then evaluate a real YOLOv8 model when available.

The 20-image flow split is intentionally rejected: it has no independent test batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from camera_service.frame_source import image_size
from waste_vision.label_check import check
from waste_vision.trial20 import CLASSES

SPLITS = ("train", "val", "test")
SCENES = ("single", "multi", "center", "edge", "angle", "occluded", "dim", "bright", "empty")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_independent_test(dataset: Path, manifest_path: Path, classes_path: Path) -> dict:
    """Check split provenance, label presence and hashes; never infer an empty scene."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    classes = tuple(x.strip() for x in classes_path.read_text(encoding="utf-8").splitlines() if x.strip())
    if classes != CLASSES or tuple(manifest.get("classes", ())) != CLASSES:
        raise ValueError("class order differs from the four confirmed block labels")
    if manifest.get("purpose") != "independent_test" or manifest.get("sealed") is not True:
        raise ValueError("a sealed independent_test manifest is required; trial20 flow is not a test set")
    entries = manifest.get("splits")
    if not isinstance(entries, dict) or any(not isinstance(entries.get(s), list) or not entries[s] for s in SPLITS):
        raise ValueError("manifest needs nonempty train, val and test lists")
    seen_hashes: dict[str, str] = {}
    group_split: dict[str, str] = {}
    counts: dict[str, dict] = {}
    scene_counts = Counter()
    test_paths = []
    test_tags_by_file = {}
    for split in SPLITS:
        images_dir, labels_dir = dataset / "images" / split, dataset / "labels" / split
        label_check = check(images_dir, labels_dir, classes_path)
        if not label_check["ready_for_training"]:
            raise ValueError(f"{split} images/labels fail format checks: {label_check}")
        rows = entries[split]
        observed = {p.name for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}}
        listed = [row.get("file") for row in rows]
        if len(listed) != len(set(listed)) or set(listed) != observed:
            raise ValueError(f"{split} manifest names do not exactly match images")
        counts[split] = {"images": len(rows), "class_boxes": label_check["class_box_counts"], "verified_empty": 0}
        for row in rows:
            name, group = row["file"], row.get("capture_group")
            if Path(name).name != name or not group or not isinstance(group, str):
                raise ValueError(f"invalid filename or capture_group: {row}")
            if group in group_split and group_split[group] != split:
                raise ValueError(f"capture group {group!r} crosses {group_split[group]} and {split}")
            group_split[group] = split
            image = images_dir / name
            label = labels_dir / f"{Path(name).stem}.txt"
            image_hash, label_hash = digest(image), digest(label)
            if image_hash != row.get("image_sha256") or label_hash != row.get("label_sha256"):
                raise ValueError(f"image or label changed after manifest was sealed: {split}/{name}")
            if image_hash in seen_hashes:
                raise ValueError(f"exact duplicate across dataset: {seen_hashes[image_hash]} and {split}/{name}")
            seen_hashes[image_hash] = f"{split}/{name}"
            empty = not label.read_text(encoding="utf-8").strip()
            tags = row.get("scene_tags")
            if not isinstance(tags, list) or any(tag not in SCENES for tag in tags):
                raise ValueError(f"invalid scene_tags: {split}/{name}")
            if empty and (row.get("empty_verified") is not True or "empty" not in tags):
                raise ValueError(f"empty label lacks explicit human verification: {split}/{name}")
            if not empty and (row.get("empty_verified") is True or "empty" in tags):
                raise ValueError(f"nonempty label marked empty: {split}/{name}")
            if empty:
                counts[split]["verified_empty"] += 1
            object_count = len(label.read_text(encoding="utf-8").splitlines())
            if ("single" in tags and object_count != 1) or ("multi" in tags and object_count < 2):
                raise ValueError(f"single/multi tag disagrees with human boxes: {split}/{name}")
            if split == "test":
                test_paths.append(image)
                test_tags_by_file[name] = tags
                scene_counts.update(tags)
    missing_classes = [name for name in CLASSES if not counts["test"]["class_boxes"].get(name)]
    return {"status": "audit_passed", "dataset": str(dataset.resolve()),
            "manifest": str(manifest_path.resolve()), "manifest_sha256": digest(manifest_path),
            "classes": CLASSES, "splits": counts, "test_scene_counts": dict(scene_counts),
            "missing_test_classes": missing_classes,
            "missing_test_scenes": [scene for scene in SCENES if scene_counts[scene] == 0],
            "near_duplicate_review": "Capture groups checked; visually review near duplicates before accepting the test batch.",
            "test_paths": test_paths, "test_tags_by_file": test_tags_by_file}


def summarize_ms(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"n": len(values), "mean_ms": round(statistics.mean(values), 3),
            "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 3)} if values else {"n": 0}


def ground_truth_boxes(label: Path, width: int, height: int) -> list[dict]:
    boxes = []
    for line in label.read_text(encoding="utf-8").splitlines():
        class_text, cx, cy, bw, bh = line.split()
        class_id = int(class_text)
        cx, cy, bw, bh = map(float, (cx, cy, bw, bh))
        boxes.append({"class_id": class_id,
                      "xyxy": [(cx-bw/2)*width, (cy-bh/2)*height,
                               (cx+bw/2)*width, (cy+bh/2)*height]})
    return boxes


def box_iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2-x1) * max(0.0, y2-y1)
    area_a = (a[2]-a[0]) * (a[3]-a[1])
    area_b = (b[2]-b[0]) * (b[3]-b[1])
    return intersection / (area_a + area_b - intersection) if intersection else 0.0


def match_detections(predictions: list[dict], truth: list[dict], min_iou: float = 0.5) -> dict:
    """One-to-one class-aware matching at the locked operating confidence threshold."""
    matched = set()
    by_class = {name: {"tp": 0, "fp": 0, "fn": 0} for name in CLASSES}
    for pred in sorted(predictions, key=lambda item: item["confidence"], reverse=True):
        class_id = CLASSES.index(pred["raw_label"])
        options = [(box_iou(pred["bbox_xyxy_raw_camera"], gt["xyxy"]), index)
                   for index, gt in enumerate(truth) if index not in matched and gt["class_id"] == class_id]
        best_iou, best_index = max(options, default=(0.0, -1))
        if best_iou >= min_iou:
            matched.add(best_index)
            by_class[CLASSES[class_id]]["tp"] += 1
        else:
            by_class[CLASSES[class_id]]["fp"] += 1
    for index, gt in enumerate(truth):
        if index not in matched:
            by_class[CLASSES[gt["class_id"]]]["fn"] += 1
    return by_class


def evaluate(audit: dict, data_yaml: Path, weights: Path, thresholds: Path,
             output: Path, device: str, imgsz: int, warmup: int) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite prior evaluation: {output}")
    if not weights.is_file():
        raise FileNotFoundError(weights)
    lock = json.loads(thresholds.read_text(encoding="utf-8"))
    if lock.get("selected_on") != "val" or not lock.get("selection_notes"):
        raise ValueError("confidence/NMS thresholds must be selected on validation data and documented")
    conf, iou = float(lock["confidence"]), float(lock["nms_iou"])
    if not (0 < conf < 1 and 0 < iou < 1):
        raise ValueError("confidence and NMS IoU must be in (0,1)")
    if not data_yaml.is_file():
        raise FileNotFoundError(data_yaml)
    import yaml
    data_config = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    configured_root = Path(data_config.get("path", ""))
    if not configured_root.is_absolute():
        configured_root = data_yaml.parent / configured_root
    if configured_root.resolve() != Path(audit["dataset"]).resolve():
        raise ValueError("data.yaml path differs from the audited dataset root")
    if any(data_config.get(split) != f"images/{split}" for split in SPLITS):
        raise ValueError("data.yaml must use the audited train/val/test directories")
    declared_names = data_config.get("names")
    if isinstance(declared_names, dict):
        declared_names = [declared_names.get(i, declared_names.get(str(i))) for i in range(4)]
    if tuple(declared_names or ()) != CLASSES:
        raise ValueError("data.yaml class order differs from the confirmed classes")
    if device != "cpu" and device != "0":
        raise ValueError("device must be cpu or 0")
    import torch
    from ultralytics import YOLO, __version__ as ultralytics_version
    if device == "0" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    output.mkdir(parents=True)
    (output / "overlays").mkdir()
    loaded_at = time.perf_counter()
    model = YOLO(str(weights.resolve()))
    loading_ms = (time.perf_counter() - loaded_at) * 1000
    names = tuple(model.names[i] for i in range(4))
    if names != CLASSES:
        raise ValueError(f"model class order differs: {names}")
    def sync() -> None:
        if device == "0":
            torch.cuda.synchronize()
    val_images = sorted(p for p in (Path(audit["dataset"]) / "images" / "val").iterdir()
                        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    first = val_images[0]
    for _ in range(warmup):
        model.predict(source=str(first), imgsz=imgsz, conf=conf, iou=iou, device=device, verbose=False)
    sync()
    metrics = model.val(data=str(data_yaml.resolve()), split="test", imgsz=imgsz,
                        conf=0.001, iou=iou, device=device, plots=True, verbose=False,
                        project=str(output.resolve()), name="ultralytics_test", exist_ok=True)
    per_class = {}
    for index, class_id in enumerate(metrics.box.ap_class_index):
        _, _, ap50, ap5095 = metrics.class_result(index)
        per_class[CLASSES[int(class_id)]] = {"ap50": float(ap50), "ap50_95": float(ap5095)}
    for name in CLASSES:
        per_class.setdefault(name, {"ap50": None, "ap50_95": None})
    operating_counts = {name: {"tp": 0, "fp": 0, "fn": 0} for name in CLASSES}
    scene_matches = {scene: {"images": 0, "tp": 0, "fp": 0, "fn": 0} for scene in SCENES}
    times = {key: [] for key in ("preprocess", "inference", "postprocess", "request")}
    cases = []
    for image in audit["test_paths"]:
        sync()
        started = time.perf_counter()
        predictions = model.predict(source=str(image), imgsz=imgsz, conf=conf,
                                    iou=iou, device=device, verbose=False)
        sync()
        times["request"].append((time.perf_counter() - started) * 1000)
        if len(predictions) != 1:
            raise RuntimeError(f"unexpected prediction count for {image}")
        result = predictions[0]
        width, height = image_size(image.read_bytes())
        if result.orig_shape != (height, width):
            raise ValueError(f"model result shape is not original image shape: {image}")
        boxes = []
        for box in result.boxes:
            x1, y1, x2, y2 = [float(x) for x in box.xyxy[0].tolist()]
            class_id = int(box.cls[0])
            if not (0 <= class_id < 4 and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError(f"illegal raw image coordinate or class: {image}, {class_id}, {(x1,y1,x2,y2)}")
            boxes.append({"raw_label": CLASSES[class_id], "confidence": float(box.conf[0]),
                          "bbox_xyxy_raw_camera": [x1,y1,x2,y2],
                          "center_x": (x1+x2)/2, "center_y": (y1+y2)/2})
        for key in ("preprocess", "inference", "postprocess"):
            times[key].append(float(result.speed[key]))
        label = Path(audit["dataset"]) / "labels" / "test" / f"{image.stem}.txt"
        truth = ground_truth_boxes(label, width, height)
        matched = match_detections(boxes, truth)
        for name in CLASSES:
            for key in ("tp", "fp", "fn"):
                operating_counts[name][key] += matched[name][key]
        tags = audit["test_tags_by_file"][image.name]
        for scene in tags:
            scene_matches[scene]["images"] += 1
            for key in ("tp", "fp", "fn"):
                scene_matches[scene][key] += sum(matched[name][key] for name in CLASSES)
        import cv2
        annotated = result.plot()
        for gt in truth:
            x1, y1, x2, y2 = (round(v) for v in gt["xyxy"])
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(annotated, "GT " + CLASSES[gt["class_id"]], (x1, max(12, y1-4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        overlay = output / "overlays" / image.name
        if not cv2.imwrite(str(overlay), annotated):
            raise OSError(f"failed to save overlay: {overlay}")
        needs_review = any(matched[name]["fp"] or matched[name]["fn"] for name in CLASSES)
        cases.append({"file": image.name, "scene_tags": tags,
                      "ground_truth_count": len(truth),
                      "prediction_count": len(boxes), "review_required": bool(needs_review),
                      "per_class_matches": matched, "boxes": boxes,
                      "overlay": str(overlay.resolve())})
    for name in CLASSES:
        tp, fp, fn = (operating_counts[name][key] for key in ("tp", "fp", "fn"))
        per_class[name]["precision_at_locked_conf"] = tp/(tp+fp) if tp+fp else None
        per_class[name]["recall_at_locked_conf"] = tp/(tp+fn) if tp+fn else None
        per_class[name]["tp_fp_fn_at_locked_conf"] = operating_counts[name]
    for scene, counts in scene_matches.items():
        tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
        counts["precision_at_locked_conf"] = tp/(tp+fp) if tp+fp else None
        counts["recall_at_locked_conf"] = tp/(tp+fn) if tp+fn else None
    coverage_gaps = audit["missing_test_classes"] or audit["missing_test_scenes"]
    return {"created_at": datetime.now(timezone.utc).isoformat(),
            "model": str(weights.resolve()), "model_sha256": digest(weights),
            "data_yaml": str(data_yaml.resolve()), "threshold_lock_sha256": digest(thresholds),
            "audit": {key: value for key, value in audit.items() if key != "test_paths"},
            "device": device, "platform": platform.platform(), "torch": torch.__version__,
            "ultralytics": ultralytics_version, "input_size": imgsz, "warmup_runs": warmup,
            "model_load_ms": round(loading_ms, 3), "confidence": conf, "nms_iou": iou,
            "map50": float(metrics.box.map50), "ap_metric_confidence_floor": 0.001,
            "operating_match_iou": 0.5, "per_class": per_class,
            "per_scene_at_locked_conf": scene_matches,
            "stage_speed": {key: summarize_ms(values) for key, value in times.items()},
            "api_end_to_end": "not measured here; requires real HTTP backend integration",
            "cases": cases,
            "effect_verdict": "evidence_insufficient" if coverage_gaps else "manual_review_required",
            "effect_note": "Metrics alone never establish adequate grasp localization or business category mapping."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--classes", type=Path, default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--data-yaml", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--threshold-lock", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device", choices=("cpu", "0"), default="0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    audit = audit_independent_test(args.dataset, args.manifest, args.classes)
    if args.audit_only:
        audit.pop("test_paths")
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return
    if not all((args.data_yaml, args.weights, args.threshold_lock, args.output)):
        parser.error("real evaluation requires --data-yaml, --weights, --threshold-lock and --output")
    if args.imgsz <= 0 or args.warmup < 0:
        parser.error("imgsz must be positive and warmup nonnegative")
    report = evaluate(audit, args.data_yaml, args.weights, args.threshold_lock,
                      args.output, args.device, args.imgsz, args.warmup)
    (args.output / "effect_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output / "effect_result.json"),
                      "effect_verdict": report["effect_verdict"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
