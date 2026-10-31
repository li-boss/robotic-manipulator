"""Export the human-reviewed 20 images for a training-pipeline smoke run only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from waste_vision.label_check import check
from waste_vision.trial20 import CLASSES

VAL_FILES = ("193.jpg", "213.jpg", "279.jpg", "414.jpg")


def export(trial: Path, classes_file: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite smoke dataset: {output}")
    manifest = json.loads((trial / "manifest.json").read_text(encoding="utf-8"))
    names = [row["filename"] for row in manifest["images"]]
    classes = tuple(x.strip() for x in classes_file.read_text(encoding="utf-8").splitlines() if x.strip())
    if len(names) != 20 or len(set(names)) != 20 or tuple(manifest["classes"]) != CLASSES or classes != CLASSES:
        raise ValueError("trial size or confirmed class order changed")
    result = check(trial / "images", trial / "labels", classes_file)
    pending_path = trial / "pending_review.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else []
    if not result["ready_for_training"] or pending:
        raise ValueError("all 20 human labels must pass format checks with no pending review")
    for name in names:
        label = trial / "labels" / f"{Path(name).stem}.txt"
        if not label.read_text(encoding="utf-8").strip():
            raise ValueError(f"trial image has no object box: {name}")
    if not set(VAL_FILES) <= set(names):
        raise ValueError("fixed flow validation files missing")
    for row in manifest["images"]:
        image = trial / "images" / row["filename"]
        if hashlib.sha256(image.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"trial image changed from source: {image}")
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True)
        (output / "labels" / split).mkdir(parents=True)
    records = []
    for name in names:
        split = "val" if name in VAL_FILES else "train"
        src_image = trial / "images" / name
        src_label = trial / "labels" / f"{Path(name).stem}.txt"
        shutil.copy2(src_image, output / "images" / split / name)
        shutil.copy2(src_label, output / "labels" / split / src_label.name)
        records.append({"image": name, "split": split,
                        "image_sha256": hashlib.sha256(src_image.read_bytes()).hexdigest(),
                        "label_sha256": hashlib.sha256(src_label.read_bytes()).hexdigest()})
    yaml = f'path: "{output.resolve().as_posix()}"\ntrain: images/train\nval: images/val\nnames:\n'
    yaml += "".join(f"  {i}: {name}\n" for i, name in enumerate(CLASSES))
    (output / "data.yaml").write_text(yaml, encoding="utf-8")
    provenance = {"purpose": "training pipeline smoke run ONLY; not independent validation or accuracy evidence",
                  "source": str(trial.resolve()), "train_count": 16, "val_count": 4,
                  "val_files": VAL_FILES, "classes": CLASSES, "files": records,
                  "warning": "same collection session; validation metrics must not be reported as generalization accuracy"}
    (output / "split.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"train": 16, "val": 4, "output": str(output), "purpose": provenance["purpose"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, default=Path("data/a_blocks/trial20"))
    parser.add_argument("--classes", type=Path, default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--output", type=Path, default=Path("data/a_blocks/trial20_flow"))
    args = parser.parse_args()
    print(json.dumps(export(args.trial, args.classes, args.output), ensure_ascii=False, indent=2))
