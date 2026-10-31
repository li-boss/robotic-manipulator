"""Evaluate detection, top-face localization and full flow against full-frame human audit.

Only frames explicitly marked complete enter denominators. No synthetic truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from tools.top_face_validate import checked_reference_center, p95


def box_iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection) if area_a + area_b > intersection else 0


def match_boxes(truth, predictions, min_iou=.5):
    """Maximum cardinality first, then maximum total IoU; stable index tie order."""
    n, m = len(truth), len(predictions)
    if not n:
        return [], [], [], list(range(m))
    ious = [[box_iou(obj["bbox"], pred["bbox"]) for pred in predictions] for obj in truth]
    # Rectangular Hungarian assignment: each GT has a private dummy column.
    # One valid match outweighs the maximum possible sum of IoUs elsewhere.
    bonus = n + 1
    cost = [[-(bonus + ious[i][j]) if ious[i][j] >= min_iou else bonus * 2
             for j in range(m)] + [0] * n for i in range(n)]
    cols = m + n
    u, v, p, way = [0.0] * (n + 1), [0.0] * (cols + 1), [0] * (cols + 1), [0] * (cols + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0, minv, used = 0, [float("inf")] * (cols + 1), [False] * (cols + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = {p[j] - 1: j - 1 for j in range(1, cols + 1) if p[j]}
    matches = sorted((gi, pi, ious[gi][pi]) for gi, pi in assignment.items()
                     if pi < m and ious[gi][pi] >= min_iou)
    used_gt = {gi for gi, _, _ in matches}
    used_pred = {pi for _, pi, _ in matches}
    missed = [gi for gi in range(len(truth)) if gi not in used_gt]
    duplicate, background = [], []
    for pi, pred in enumerate(predictions):
        if pi in used_pred:
            continue
        if any(box_iou(pred["bbox"], truth[gi]["bbox"]) >= min_iou for gi in used_gt):
            duplicate.append(pi)
        else:
            background.append(pi)
    return matches, missed, duplicate, background


def evaluate(dataset: Path, audit_path: Path, min_iou: float, max_error_px: float | None):
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    schema = audit.get("schema_version")
    if schema not in {"full-frame-human-audit-v1", "full-frame-ai-audit-v1"}:
        raise ValueError("expected full-frame human or AI audit reference file")
    is_ai = schema == "full-frame-ai-audit-v1"
    if is_ai and (audit.get("review_source") != "AI" or audit.get("human_confirmed") is not False):
        raise ValueError("AI audit must explicitly declare source and human_confirmed=false")
    totals = Counter()
    errors, cases = [], []
    class_totals = {name: Counter() for name in
                    ("block_blue", "block_green", "block_red", "block_yellow")}
    for result_path in sorted(dataset.glob("*/result.json")):
        record = json.loads(result_path.read_text(encoding="utf-8"))
        case_id = record["case_id"]
        review = audit["frames"].get(case_id)
        if not review or review.get("review_status") != ("ai_complete" if is_ai else "complete"):
            continue
        if is_ai and (review.get("review_source") != "AI" or review.get("human_confirmed") is not False):
            raise ValueError(f"AI frame source missing: {case_id}")
        raw = (result_path.parent / "raw.jpg").read_bytes()
        if (review.get("source_sha256") != hashlib.sha256(raw).hexdigest() or
                review.get("frame_id") != record["frame_id"] or
                review.get("image_width") != record["image_width"] or
                review.get("image_height") != record["image_height"]):
            raise ValueError(f"human audit frame provenance mismatch: {case_id}")
        truth, predictions = review["objects"], record["targets"]
        if len({obj.get("object_id") for obj in truth}) != len(truth) and is_ai:
            raise ValueError(f"duplicate AI object ID: {case_id}")
        for obj in truth:
            if obj["raw_label"] not in class_totals:
                raise ValueError(f"unknown human label: {case_id}")
            if (len(obj["bbox"]) != 4 or
                    not (0 <= obj["bbox"][0] < obj["bbox"][2] <= record["image_width"] and
                         0 <= obj["bbox"][1] < obj["bbox"][3] <= record["image_height"])):
                raise ValueError(f"invalid human box: {case_id}")
        matches, missed, duplicate, background = match_boxes(truth, predictions, min_iou)
        totals["frames"] += 1
        totals["true_objects"] += len(truth)
        totals["predictions"] += len(predictions)
        totals["matched"] += len(matches)
        totals["missed"] += len(missed)
        totals["duplicate_predictions"] += len(duplicate)
        totals["background_false_positives"] += len(background)
        totals["complete_true_tops"] += sum(obj["top_visibility"] == "complete" for obj in truth)
        totals["incomplete_true_tops"] += sum(obj["top_visibility"] == "incomplete" for obj in truth)
        totals["uncertain_true_tops"] += sum(obj["top_visibility"] == "uncertain" for obj in truth)
        detail = {"case_id": case_id, "capture_batch": case_id.split("__")[0],
                  "true_objects": len(truth), "predictions": len(predictions),
                  "missed_gt_indices": missed, "duplicate_prediction_indices": duplicate,
                  "background_prediction_indices": background, "matches": []}
        for gi, obj in enumerate(truth):
            class_totals[obj["raw_label"]]["true"] += 1
            if gi in missed:
                class_totals[obj["raw_label"]]["missed"] += 1
        for gi, pi, iou in matches:
            obj, pred = truth[gi], predictions[pi]
            label_correct = obj["raw_label"] == pred["raw_label"]
            if not label_correct:
                totals["wrong_class"] += 1
                class_totals[obj["raw_label"]]["wrong_class"] += 1
            else:
                class_totals[obj["raw_label"]]["matched_correct_class"] += 1
            face = pred.get("top_face") or {}
            candidate = face.get("candidate_point") if face.get("status") == "candidate" else None
            visibility = obj["top_visibility"]
            match = {"gt_index": gi, "object_id": obj.get("object_id"),
                     "prediction_index": pi, "iou": round(iou, 4),
                     "gt_label": obj["raw_label"], "predicted_label": pred["raw_label"],
                     "label_correct": label_correct, "top_visibility": visibility,
                     "top_face_status": face.get("status"), "top_face_reason": face.get("reason")}
            if visibility == "incomplete":
                totals["matched_incomplete_tops"] += 1
                if candidate:
                    totals["policy_violation_candidate_on_incomplete"] += 1
                    match["policy_violation"] = True
                else:
                    totals["active_review_on_incomplete"] += 1
            elif visibility == "complete":
                totals["matched_complete_tops"] += 1
                reference = checked_reference_center(obj["corners"],
                                                     record["image_width"], record["image_height"])
                if candidate:
                    error = math.dist(reference, (candidate["x"], candidate["y"]))
                    match["center_error_px"] = round(error, 3)
                    errors.append(error)
                    totals["candidate_on_complete"] += 1
                    if max_error_px is not None:
                        if error <= max_error_px:
                            totals["accurate_location"] += 1
                            if label_correct:
                                totals["overall_correct"] += 1
                        else:
                            totals["offset_candidate"] += 1
                else:
                    totals["review_on_complete"] += 1
            elif visibility != "uncertain":
                raise ValueError(f"invalid top visibility: {case_id}")
            if candidate:
                totals["candidate_on_all_true"] += 1
            detail["matches"].append(match)
        cases.append(detail)
    n = totals["frames"]
    if n == 0:
        return {"status": "未评估", "reason": "没有完成的全图参考审查",
                "reference_source": "AI" if is_ai else "human",
                "human_confirmed": not is_ai,
                "match_iou_threshold": min_iou, "max_center_error_px": max_error_px,
                "completed_frames": 0, "detection": None, "localization": None,
                "overall": None, "cases": []}
    return {"status": ("相对AI参考标注的开发评估" if is_ai else
                       "已评估人工完成的帧；不代表独立测试"),
            "reference_source": "AI" if is_ai else "human", "human_confirmed": not is_ai,
            "match_iou_threshold": min_iou, "max_center_error_px": max_error_px,
            "completed_frames": n,
            "detection": {key: totals[key] for key in
                          ("true_objects", "predictions", "matched", "missed", "wrong_class",
                           "duplicate_predictions", "background_false_positives")},
            "by_true_class": {name: (dict(counts) if counts["true"] else {"status": "未评估"})
                              for name, counts in class_totals.items()},
            "localization": {"matched_complete_tops": totals["matched_complete_tops"],
                             "complete_true_tops": totals["complete_true_tops"],
                             "candidate_on_complete": totals["candidate_on_complete"],
                             "candidate_coverage_all_complete":
                                 (round(totals["candidate_on_complete"] / totals["complete_true_tops"], 4)
                                  if totals["complete_true_tops"] else None),
                             "candidate_coverage_matched_complete":
                                 (round(totals["candidate_on_complete"] / totals["matched_complete_tops"], 4)
                                  if totals["matched_complete_tops"] else None),
                             "review_on_complete": totals["review_on_complete"],
                             "incomplete_true_tops": totals["incomplete_true_tops"],
                             "matched_incomplete_tops": totals["matched_incomplete_tops"],
                             "active_review_on_incomplete": totals["active_review_on_incomplete"],
                             "policy_violation_candidate_on_incomplete":
                                 totals["policy_violation_candidate_on_incomplete"],
                             "incomplete_false_accept_rate_all":
                                 (round(totals["policy_violation_candidate_on_incomplete"] /
                                        totals["incomplete_true_tops"], 4)
                                  if totals["incomplete_true_tops"] else None),
                             "uncertain_true_tops_excluded_from_error": totals["uncertain_true_tops"],
                             "center_error_sample_n": len(errors),
                             "center_error_median_px": round(statistics.median(errors), 3) if errors else None,
                             "center_error_p95_px": round(p95(errors), 3) if errors else None,
                             "center_error_max_px": round(max(errors), 3) if errors else None,
                             "accurate_location": totals["accurate_location"] if max_error_px is not None else None,
                             "accuracy_over_all_complete":
                                 (round(totals["accurate_location"] / totals["complete_true_tops"], 4)
                                  if max_error_px is not None and totals["complete_true_tops"] else None),
                             "offset_candidate": totals["offset_candidate"] if max_error_px is not None else None},
            "overall": {"true_object_denominator": totals["true_objects"],
                        "candidate_output_count": totals["candidate_on_all_true"],
                        "candidate_output_fraction":
                            (round(totals["candidate_on_all_true"] / totals["true_objects"], 4)
                             if totals["true_objects"] else None),
                        "candidate_output_note": "合理review也降低此比例；不完整顶面的错误接受可能提高此比例",
                        "correct_top_candidate": totals["overall_correct"] if max_error_px is not None else None,
                        "fraction": (round(totals["overall_correct"] / totals["true_objects"], 4)
                                     if max_error_px is not None and totals["true_objects"] else None)},
            "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--match-iou", type=float, default=.5)
    parser.add_argument("--max-center-error-px", type=float,
                        help="predeclared validation threshold; omit to report distances without pass/fail")
    args = parser.parse_args()
    if not 0 < args.match_iou <= 1 or args.max_center_error_px is not None and args.max_center_error_px <= 0:
        parser.error("invalid matching or center error threshold")
    result = evaluate(Path(args.dataset), Path(args.audit), args.match_iou, args.max_center_error_px)
    path = Path(args.out)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "completed_frames", "detection", "overall")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
