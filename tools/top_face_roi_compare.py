"""Reproducible ROI expansion diagnostic on one archived real camera image.

The tight box is deliberately modified from the archived YOLO box; it is a
stress test of crop tolerance, not a claim about actual YOLO output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.camera_field_test import save_overlay
from waste_vision.top_face import ALGORITHM_VERSION, TopFaceLocator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="archived frame folder with raw.jpg/response.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--weights", default="data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01/weights/best.pt")
    args = parser.parse_args()
    source, out = Path(args.source), Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    raw = (source / "raw.jpg").read_bytes()
    response = json.loads((source / "response.json").read_text(encoding="utf-8"))["response"]
    target = dict(response["targets"][0])
    original_box = list(target["bbox"])
    # 7-10 px inset at the top / left of the visible blue top face.
    target["bbox"] = [135.0, 115.0, 340.0, 350.0]
    config = json.loads(Path("configs/a_top_face_hsv.json").read_text(encoding="utf-8"))
    strict_path = out / "zero_expansion_config.json"
    config["roi_expand_fraction"] = 0
    strict_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    results = {}
    for name, locator in (("before_zero_expansion", TopFaceLocator(strict_path)),
                          ("after_expansion", TopFaceLocator("configs/a_top_face_hsv.json"))):
        face, ms = locator.locate(raw, response["image_width"], response["image_height"], [target])
        shown = dict(target, top_face=face[0])
        save_overlay(raw, [shown], out / f"{name}.jpg", show_box_center=True)
        results[name] = {"top_face": face[0], "top_face_ms": ms,
                         "config_sha256": locator.config_sha256}
    (out / "raw.jpg").write_bytes(raw)
    report = {"source": str(source), "frame_id": response["frame_id"],
              "source_type": "saved_real_frame_with_manually_tightened_detection_box",
              "raw_sha256": hashlib.sha256(raw).hexdigest(),
              "weights_sha256": hashlib.sha256(Path(args.weights).read_bytes()).hexdigest(),
              "weights_provenance": "declared formal weight; archived prediction lacks artifact hash",
              "algorithm_version": ALGORITHM_VERSION,
              "algorithm_source_sha256": hashlib.sha256(Path("waste_vision/top_face.py").read_bytes()).hexdigest(),
              "model_parameters": {"imgsz": 640, "confidence": .25, "nms_iou": .5,
                                   "provenance": "declared baseline"},
              "original_yolo_box": original_box, "diagnostic_tight_box": target["bbox"],
              "note": "diagnostic manually tightened box on real raw frame; not a real YOLO prediction",
              "results": results}
    (out / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {"status": value["top_face"]["status"],
                             "reason": value["top_face"]["reason"],
                             "candidate_point": value["top_face"]["candidate_point"]}
                      for name, value in results.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
