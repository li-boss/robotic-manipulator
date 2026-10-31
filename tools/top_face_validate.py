"""Replay saved real-camera frames through the experimental top-face locator.

Uses the archived detection response and its exact raw JPEG. Manual reference
points are separate from model output; absent references never become truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from tools.camera_field_test import save_overlay
from waste_vision.top_face import ALGORITHM_VERSION, TopFaceLocator, diagonal_intersection


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def p95(values):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(.95 * len(ordered)) - 1)] if ordered else None


def checked_reference_center(corners, width, height):
    if not isinstance(corners, list) or len(corners) != 4 or any(
            not isinstance(p, list) or len(p) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in p) or
            not (0 <= p[0] < width and 0 <= p[1] < height) for p in corners):
        raise ValueError("reference corners must be four in-image points")
    turns = []
    for index in range(4):
        a, b, c = (corners[(index + offset) % 4] for offset in (0, 1, 2))
        turns.append((b[0] - a[0]) * (c[1] - b[1]) -
                     (b[1] - a[1]) * (c[0] - b[0]))
    if min(abs(v) for v in turns) < 1 or not (all(v > 0 for v in turns) or all(v < 0 for v in turns)):
        raise ValueError("reference corners must be a nondegenerate convex outline in order")
    return diagonal_intersection(corners)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True,
                        help="folder containing saved raw.jpg and response.json pairs; repeatable")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default="configs/a_top_face_hsv.json")
    parser.add_argument("--references", help="optional manually reviewed reference corners")
    parser.add_argument("--weights", default="data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01/weights/best.pt")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=.25)
    parser.add_argument("--nms-iou", type=float, default=.5)
    parser.add_argument("--origin", choices=["atlas_camera_saved", "other_saved_image", "unknown"],
                        default="unknown", help="where the archived raw image originally came from")
    parser.add_argument("--rerun-model", action="store_true",
                        help="run the formal weight again on each saved raw image; otherwise use archived predictions")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    locator = TopFaceLocator(args.config)
    weights_path = Path(args.weights)
    if not weights_path.is_file():
        raise FileNotFoundError(weights_path)
    provenance = {
        "validation_mode": "saved_frame_replay_rerun_model" if args.rerun_model else
                           "saved_frame_replay_archived_predictions",
        "raw_origin": args.origin,
        "weights_path": str(weights_path.resolve()),
        "weights_sha256": hashlib.sha256(weights_path.read_bytes()).hexdigest(),
        "top_face_config_path": str(Path(args.config).resolve()),
        "top_face_config_sha256": hashlib.sha256(Path(args.config).read_bytes()).hexdigest(),
        "top_face_config_version": locator.version,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_source_sha256": hashlib.sha256(Path("waste_vision/top_face.py").read_bytes()).hexdigest(),
        "model_parameters": {"imgsz": args.imgsz, "confidence": args.conf, "nms_iou": args.nms_iou,
                             "parameter_provenance": "actual_rerun" if args.rerun_model else
                                                     "declared_baseline_not_verified_from_archive"},
        "captured_at_semantics": "archived A computer publication time; not original sensor exposure time",
    }
    backend = None
    if args.rerun_model:
        from waste_vision.yolov8_backend import YoloV8nBackend
        backend = YoloV8nBackend(weights_path, imgsz=args.imgsz,
                                confidence=args.conf, nms_iou=args.nms_iou)
        backend.warmup()
    references = (json.loads(Path(args.references).read_text(encoding="utf-8"))
                  if args.references else {})
    rows = []
    jitter = defaultdict(list)
    for root_name in args.root:
        root = Path(root_name)
        for response_path in sorted(root.rglob("response.json")):
            source = response_path.parent
            raw_path = source / "raw.jpg"
            if not raw_path.is_file():
                continue
            record = json.loads(response_path.read_text(encoding="utf-8"))
            response = record.get("response", {})
            if response.get("simulated") is not False or "targets" not in response:
                continue
            case_id = root.name + "__" + "__".join(source.relative_to(root).parts)
            case = out / case_id
            case.mkdir(parents=True, exist_ok=False)
            raw = raw_path.read_bytes()
            shutil.copyfile(raw_path, case / "raw.jpg")
            if backend is None:
                targets = [dict(target) for target in response["targets"]]
            else:
                from camera_service.camera import Frame
                from waste_vision.detector import Detector
                frame = Frame(response["frame_id"], response["camera_id"],
                              response["captured_at"], response["image_width"],
                              response["image_height"], "raw_camera", "jpeg", raw)
                rerun = Detector(backend, "configs/a_category_mapping.json").detect(frame)
                targets = rerun["targets"]
            faces, face_ms = locator.locate(raw, response["image_width"],
                                            response["image_height"], targets)
            item_rows = []
            for index, (target, face) in enumerate(zip(targets, faces)):
                target["top_face"] = face
                row = {"index": index, "raw_label": target["raw_label"],
                       "bbox_center": [target["center_x"], target["center_y"]],
                       "status": face["status"], "reason": face["reason"],
                       "candidate_point": face["candidate_point"],
                       "visible_centroid": face["visible_centroid"]}
                ref = references.get(case_id, {}).get(str(index))
                if ref:
                    row["reference_status"] = ref.get("review_status", "unverified_legacy")
                    if ref.get("review_status") in {"confirmed", "incomplete", "uncertain"}:
                        if (ref.get("reference_version") != "manual-raw-corners-v2" or
                                ref.get("source_sha256") != hashlib.sha256(raw).hexdigest() or
                                ref.get("frame_id") != response["frame_id"] or
                                ref.get("image_width") != response["image_width"] or
                                ref.get("image_height") != response["image_height"]):
                            raise ValueError(f"reference provenance mismatch: {case_id} target {index}")
                    if ref.get("review_status") == "confirmed" and ref.get("corners"):
                        reference_center = checked_reference_center(
                            ref["corners"], response["image_width"], response["image_height"])
                        row["reference_center"] = [round(v, 3) for v in reference_center]
                        row["reference_note"] = ref.get("note")
                        row["bbox_error_px"] = round(distance(tuple(row["bbox_center"]), reference_center), 3)
                        if face["candidate_point"]:
                            point = face["candidate_point"]
                            row["top_face_error_px"] = round(distance((point["x"], point["y"]),
                                                                    reference_center), 3)
                item_rows.append(row)
                if len(targets) == 1 and face["candidate_point"]:
                    jitter[(root.name, source.relative_to(root).parts[0])].append(
                        (target["center_x"], target["center_y"],
                         face["candidate_point"]["x"], face["candidate_point"]["y"]))
            save_overlay(raw, targets, case / "segmentation.jpg", view="segmentation")
            save_overlay(raw, targets, case / "comparison.jpg", show_box_center=True)
            output = {"case_id": case_id, "source_response": str(response_path.resolve()),
                      "frame_id": response["frame_id"], "captured_at": response["captured_at"],
                      "image_width": response["image_width"], "image_height": response["image_height"],
                      "coordinate_space": response["coordinate_space"],
                      "source_sha256": hashlib.sha256(raw).hexdigest(),
                      "provenance": provenance,
                      "top_face_config_version": locator.version, "top_face_ms": face_ms,
                      "targets": targets, "reference_comparison": item_rows}
            (case / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append({"case_id": case_id, "source": str(source.resolve()),
                         "capture_batch": root.name,
                         "scene": source.relative_to(root).parts[0],
                         "target_count": len(targets), "top_face_ms": face_ms,
                         "targets": item_rows})
    status_counts = Counter(t["status"] for row in rows for t in row["targets"])
    reason_counts = Counter(t["reason"] for row in rows for t in row["targets"] if t["reason"])
    matched = [t for row in rows for t in row["targets"] if "top_face_error_px" in t]
    jitter_rows = []
    for (root, scene), values in sorted(jitter.items()):
        if len(values) < 2:
            continue
        box_x, box_y, top_x, top_y = zip(*values)
        jitter_rows.append({"root": root, "scene": scene, "frames": len(values),
                            "bbox_center_std_xy_px": [round(statistics.pstdev(box_x), 3),
                                                      round(statistics.pstdev(box_y), 3)],
                            "top_center_std_xy_px": [round(statistics.pstdev(top_x), 3),
                                                     round(statistics.pstdev(top_y), 3)]})
    timings = sorted(row["top_face_ms"] for row in rows)
    all_targets = [t for row in rows for t in row["targets"]]
    confirmed = [t for t in all_targets if t.get("reference_status") == "confirmed"]
    incomplete = [t for t in all_targets if t.get("reference_status") == "incomplete"]
    errors = [t["top_face_error_px"] for t in matched]
    scene_groups = {}
    for row in rows:
        key = f"{row['capture_batch']} / {row['scene']}"
        group = scene_groups.setdefault(key, {"frames": 0, "targets": 0,
                                             "candidate": 0, "review": 0})
        group["frames"] += 1
        group["targets"] += row["target_count"]
        group["candidate"] += sum(t["status"] == "candidate" for t in row["targets"])
        group["review"] += sum(t["status"] == "review" for t in row["targets"])
    summary = {"purpose": "development replay; archived predictions are not human truth",
               "provenance": provenance,
               "frames": len(rows), "targets": sum(row["target_count"] for row in rows),
               "scene_groups": scene_groups,
               "statuses": dict(status_counts), "reasons": dict(reason_counts),
               "candidate_fraction": (round(status_counts["candidate"] / len(all_targets), 4)
                                      if all_targets else None),
               "review_fraction": (round(status_counts["review"] / len(all_targets), 4)
                                   if all_targets else None),
               "confirmed_complete_references": len(confirmed),
               "incomplete_references": len(incomplete),
               "false_accepts_on_confirmed_incomplete": sum(t["status"] == "candidate" for t in incomplete),
               "active_rejections_on_confirmed_incomplete": sum(t["status"] == "review" for t in incomplete),
               "reference_targets": len(matched),
               "bbox_error_mean_px": (round(statistics.mean(t["bbox_error_px"] for t in matched), 3)
                                      if matched else None),
               "top_face_error_mean_px": (round(statistics.mean(t["top_face_error_px"] for t in matched), 3)
                                         if matched else None),
               "top_face_error_median_px": round(statistics.median(errors), 3) if errors else None,
               "top_face_error_p95_px": round(p95(errors), 3) if errors else None,
               "top_face_error_max_px": round(max(errors), 3) if errors else None,
               "complete_reference_rejected": sum(t["status"] == "review" for t in confirmed),
               "top_face_ms_median": round(statistics.median(timings), 3) if timings else None,
               "top_face_ms_p95": p95(timings),
               "jitter": jitter_rows, "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("frames", "targets", "statuses", "reasons",
                                              "reference_targets", "bbox_error_mean_px",
                                              "top_face_error_mean_px", "top_face_ms_median",
                                              "top_face_ms_p95")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
