"""Archive real camera API frames for later offline regression checks.

Archived predictions are a software baseline, never human ground truth.
This tool does not access robot motion or change the camera owner.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.camera_field_test import sample


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", args.scene):
        raise ValueError("--scene must be lowercase letters, digits, _ or -")
    if not 1 <= args.count <= 20 or args.interval < 0:
        raise ValueError("count must be 1..20 and interval must be nonnegative")
    root = Path(args.out)
    scene = root / args.scene
    scene.mkdir(parents=True, exist_ok=False)
    weights = Path(args.weights).resolve()
    if not weights.is_file():
        raise FileNotFoundError(weights)
    manifest = {
        "purpose": "real-camera development regression, not independent accuracy test",
        "scene": args.scene,
        "scene_note": args.note,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_service": args.source_service,
        "api_base_url": args.base_url,
        "weights": str(weights),
        "weights_sha256": sha256(weights),
        "settings": {"imgsz": args.imgsz, "conf": args.conf, "nms_iou": args.iou,
                     "backend": "yolov8n", "device": "Windows CPU"},
        "versions": {name: importlib.metadata.version(name) for name in
                     ("torch", "ultralytics", "opencv-python")},
        "python": platform.python_version(),
        "frames": [],
    }
    manifest_path = scene / "manifest.json"
    try:
        for index in range(args.count):
            folder = scene / f"frame_{index + 1:03d}"
            record = sample(args.base_url, folder, args.scene)
            raw = folder / "raw.jpg"
            response = record.get("response", {})
            item = {"folder": folder.name,
                    "frame_id": response.get("frame_id"),
                    "captured_at": response.get("captured_at"),
                    "detect_http": record.get("detect_http"),
                    "d_frame_same_bytes": record.get("d_frame_same_bytes"),
                    "outcome": response.get("outcome"),
                    "target_count": len(response.get("targets", [])),
                    "raw_sha256": sha256(raw) if raw.is_file() else None}
            manifest["frames"].append(item)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{args.scene} {index + 1}/{args.count}: {item['frame_id']} "
                  f"HTTP {item['detect_http']}, {item['target_count']} targets", flush=True)
            if index < args.count - 1:
                time.sleep(args.interval)
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["unique_frame_ids"] = len({item["frame_id"] for item in manifest["frames"]
                                            if item["frame_id"]})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {manifest_path}")


def replay(args: argparse.Namespace) -> None:
    """Compare current backend output with archived predictions, not true labels."""
    from camera_service.camera import FrameStore
    from camera_service.frame_source import image_size
    from waste_vision.detector import Detector
    from waste_vision.yolov8_backend import YoloV8nBackend

    root = Path(args.archive)
    manifests = sorted(root.glob("*/manifest.json"))
    if not manifests:
        raise FileNotFoundError(f"no scene manifests under {root}")
    backend = YoloV8nBackend(args.weights, imgsz=args.imgsz,
                            confidence=args.conf, nms_iou=args.iou)
    backend.warmup()
    detector = Detector(backend, args.mapping)
    store = FrameStore("offline-replay", max_age_ms=0)
    rows = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["frames"]:
            folder = manifest_path.parent / item["folder"]
            raw_path = folder / "raw.jpg"
            baseline = json.loads((folder / "response.json").read_text(encoding="utf-8"))["response"]
            raw = raw_path.read_bytes()
            width, height = image_size(raw)
            current = detector.detect(store.publish(raw, width, height))
            old = sorted(baseline.get("targets", []), key=lambda t: (t["raw_label"], t["center_x"]))
            new = sorted(current.get("targets", []), key=lambda t: (t["raw_label"], t["center_x"]))
            differences = []
            if baseline.get("outcome") != current.get("outcome") or len(old) != len(new):
                differences.append("outcome or target count changed")
            for index, (before, after) in enumerate(zip(old, new), 1):
                if (before["raw_label"], before["category"], before["target_status"]) != (
                        after["raw_label"], after["category"], after["target_status"]):
                    differences.append(f"target {index}: label, category or status changed")
                if abs(before["confidence"] - after["confidence"]) > args.conf_tolerance:
                    differences.append(f"target {index}: confidence changed")
                if any(abs(a - b) > args.pixel_tolerance for a, b in zip(before["bbox"], after["bbox"])):
                    differences.append(f"target {index}: bbox changed")
            rows.append({"scene": manifest["scene"], "folder": item["folder"],
                         "raw_sha256_matches": sha256(raw_path) == item["raw_sha256"],
                         "baseline_count": len(old), "current_count": len(new),
                         "differences": differences})
    report = {"scope": "offline software regression only; archived predictions are not truth",
              "weights": str(Path(args.weights).resolve()), "frames": len(rows),
              "changed_frames": sum(bool(r["differences"]) for r in rows),
              "hash_failures": sum(not r["raw_sha256_matches"] for r in rows),
              "rows": rows}
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['frames']} replayed; {report['changed_frames']} changed; "
          f"{report['hash_failures']} hash failures. {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    get = actions.add_parser("capture")
    get.add_argument("--base-url", default="http://127.0.0.1:8765")
    get.add_argument("--source-service", default="http://192.168.137.100:8080/stream.mjpg")
    get.add_argument("--out", required=True)
    get.add_argument("--scene", required=True)
    get.add_argument("--note", default="awaiting human review")
    get.add_argument("--count", type=int, default=3)
    get.add_argument("--interval", type=float, default=0.7)
    get.add_argument("--weights", required=True)
    get.add_argument("--imgsz", type=int, default=640)
    get.add_argument("--conf", type=float, default=0.25)
    get.add_argument("--iou", type=float, default=0.5)
    get.set_defaults(func=capture)
    check = actions.add_parser("replay")
    check.add_argument("--archive", required=True)
    check.add_argument("--report", required=True)
    check.add_argument("--weights", required=True)
    check.add_argument("--mapping", default="configs/a_category_mapping.json")
    check.add_argument("--imgsz", type=int, default=640)
    check.add_argument("--conf", type=float, default=0.25)
    check.add_argument("--iou", type=float, default=0.5)
    check.add_argument("--pixel-tolerance", type=float, default=1.0)
    check.add_argument("--conf-tolerance", type=float, default=0.02)
    check.set_defaults(func=replay)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
