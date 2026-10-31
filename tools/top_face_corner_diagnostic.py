"""Inspect a saved top-face contour; compare exploratory edge-line intersections.

This does not change the production locator or create human ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from waste_vision.top_face import ALGORITHM_VERSION, TopFaceLocator, diagonal_intersection


def line_intersection(a, b):
    pa, va = a
    pb, vb = b
    matrix = np.column_stack((va, -vb))
    if abs(np.linalg.det(matrix)) < 1e-5:
        return None
    return (pa + np.linalg.solve(matrix, pb - pa)[0] * va).tolist()


def fit_edges(points, quad):
    """Fit central contour portions near each current edge; exploratory only."""
    lines, counts = [], []
    for start, end in zip(quad, np.roll(quad, -1, axis=0)):
        vector = end - start
        size2 = float(np.dot(vector, vector))
        if size2 < 100:
            return None, []
        relative = points - start
        t = relative @ vector / size2
        distance = abs(relative[:, 0] * vector[1] - relative[:, 1] * vector[0]) / np.sqrt(size2)
        selected = points[(t >= .12) & (t <= .83) & (distance <= 15)]
        counts.append(len(selected))
        if len(selected) < 8:
            return None, counts
        vx, vy, x, y = cv2.fitLine(selected.astype(np.float32), cv2.DIST_HUBER, 0, .01, .01).flatten()
        lines.append((np.array([x, y], dtype=float), np.array([vx, vy], dtype=float)))
    corners = [line_intersection(lines[index - 1], lines[index]) for index in range(4)]
    if any(point is None for point in corners):
        return None, counts
    return corners, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--result", required=True, help="direct real model output with top_face")
    parser.add_argument("--label", default="block_yellow")
    parser.add_argument("--out", required=True)
    parser.add_argument("--batch-root", help="optional saved replay results to compare edge-fit shifts")
    parser.add_argument("--weights", default="data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01/weights/best.pt")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    raw = Path(args.raw).read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    record = json.loads(Path(args.result).read_text(encoding="utf-8"))
    target = next(t for t in record["targets"] if t["raw_label"] == args.label)
    face = target["top_face"]
    locator = TopFaceLocator("configs/a_top_face_hsv.json")
    left, top, right, bottom = face["quality"]["roi_xyxy"]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = locator._mask(hsv[top:bottom, left:right], args.label)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(float)
    contour += np.array([left, top])
    quad = np.asarray(face["quad"], dtype=float)
    fitted, counts = fit_edges(contour, quad)
    edge_center = diagonal_intersection(fitted) if fitted else None
    x1, y1, x2, y2 = 195, 155, 395, 345
    zoom = cv2.resize(image[y1:y2, x1:x2], (800, 760), interpolation=cv2.INTER_NEAREST)

    def point(value):
        return tuple(int(round(v)) for v in ((value[0] - x1) * 4, (value[1] - y1) * 4))

    cv2.polylines(zoom, [np.asarray([point(p) for p in contour], np.int32)], True, (0, 140, 255), 2)
    cv2.polylines(zoom, [np.asarray([point(p) for p in quad], np.int32)], True, (0, 230, 0), 2)
    for i, p in enumerate(quad):
        cv2.circle(zoom, point(p), 6, (0, 230, 0), -1)
        cv2.putText(zoom, f"Q{i}:{int(p[0])},{int(p[1])}",
                    (point(p)[0] + 7, point(p)[1] - 7), cv2.FONT_HERSHEY_SIMPLEX,
                    .45, (0, 80, 0), 2)
    cv2.line(zoom, point(quad[0]), point(quad[2]), (255, 0, 255), 1)
    cv2.line(zoom, point(quad[1]), point(quad[3]), (255, 0, 255), 1)
    cv2.drawMarker(zoom, point((face["candidate_point"]["x"], face["candidate_point"]["y"])),
                   (255, 0, 255), cv2.MARKER_CROSS, 25, 2)
    if fitted:
        for p in fitted:
            cv2.circle(zoom, point(p), 7, (255, 255, 0), -1)
        cv2.drawMarker(zoom, point(edge_center), (255, 255, 0), cv2.MARKER_TILTED_CROSS, 25, 2)
    for xy in ((361, 310), (370, 321)):
        cv2.circle(zoom, point(xy), 5, (0, 0, 255), -1)
        cv2.putText(zoom, f"{xy[0]},{xy[1]}", (point(xy)[0] - 90, point(xy)[1] + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 180), 2)
    cv2.imwrite(str(out / "yellow_corner_zoom.png"), zoom)
    (out / "raw.jpg").write_bytes(raw)
    report = {"source_type": "saved_frame_replay_exploratory_geometry",
              "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "weights_sha256": hashlib.sha256(Path(args.weights).read_bytes()).hexdigest(),
              "config_sha256": hashlib.sha256(Path("configs/a_top_face_hsv.json").read_bytes()).hexdigest(),
              "algorithm_version": ALGORITHM_VERSION,
              "algorithm_source_sha256": hashlib.sha256(Path("waste_vision/top_face.py").read_bytes()).hexdigest(),
              "model_parameters": {"imgsz": 640, "confidence": .25, "nms_iou": .5,
                                   "provenance": "declared; source result itself has no hash"},
              "source_result": str(Path(args.result).resolve()),
              "model_quad": face["quad"], "model_center": face["candidate_point"],
              "contour_vertices": face["visible_contour"],
              "edge_fit_corners": fitted, "edge_fit_center": edge_center,
              "edge_fit_used_points_per_side": counts,
              "warning": "edge fits depend on current quad for segment selection; not independent truth"}
    (out / "diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.batch_root:
        comparisons = []
        for result_path in sorted(Path(args.batch_root).glob("*/result.json")):
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            saved_raw = (result_path.parent / "raw.jpg").read_bytes()
            saved_image = cv2.imdecode(np.frombuffer(saved_raw, np.uint8), cv2.IMREAD_COLOR)
            saved_hsv = cv2.cvtColor(saved_image, cv2.COLOR_BGR2HSV)
            for index, item in enumerate(saved["targets"]):
                saved_face = item.get("top_face", {})
                if saved_face.get("status") != "candidate":
                    continue
                l, t, r, b = saved_face["quality"]["roi_xyxy"]
                saved_mask = locator._mask(saved_hsv[t:b, l:r], item["raw_label"])
                regions, _ = cv2.findContours(saved_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                if not regions:
                    continue
                pixels = max(regions, key=cv2.contourArea).reshape(-1, 2).astype(float)
                pixels += np.array([l, t])
                edges, used = fit_edges(pixels, np.asarray(saved_face["quad"], dtype=float))
                if not edges:
                    continue
                center = diagonal_intersection(edges)
                current = saved_face["candidate_point"]
                comparisons.append({"case_id": saved["case_id"], "target_index": index,
                                    "raw_label": item["raw_label"],
                                    "current_center": current, "edge_fit_center": center,
                                    "shift_px": float(np.hypot(center[0] - current["x"],
                                                       center[1] - current["y"])),
                                    "fit_points_per_side": used})
        (out / "edge_fit_batch.json").write_text(
            json.dumps({"source_type": "saved_frame_exploratory_comparison",
                        "reference_status": "no_user_confirmed_truth",
                        "rows": comparisons}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model_center": report["model_center"],
                      "edge_fit_center": edge_center, "fit_counts": counts}))


if __name__ == "__main__":
    main()
