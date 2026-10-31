"""Make a separate, traceable copy for manual annotation; write no labels."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(source: Path, destination: Path) -> dict:
    paths = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not paths:
        raise ValueError("no source images")
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing annotation workspace: {destination}")
    destination.mkdir(parents=True)
    records = []
    for path in paths:
        target = destination / path.name
        shutil.copy2(path, target)
        records.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"source": str(source), "images": len(records), "files": records,
                "labels": "not started; human confirmation and annotation required"}
    (destination / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"images": len(records), "destination": str(destination)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.images, args.output), ensure_ascii=False))
