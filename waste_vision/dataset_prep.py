"""Audit source photos and make contact sheets without inventing annotations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _features(image: Image.Image):
    small = image.convert("RGB").resize((16, 12), Image.Resampling.BILINEAR)
    values = list(small.tobytes())
    gray = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    pixels = list(gray.tobytes())
    dhash = sum((pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
                for y in range(8) for x in range(8))
    return values, dhash


def _distance(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _font(size: int):
    path = Path("C:/Windows/Fonts/arial.ttf")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def contact_sheet(paths: list[Path], output: Path, columns: int = 4,
                  tile_width: int = 300, tile_height: int = 250) -> None:
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font(17)
    for index, path in enumerate(paths):
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                thumb = ImageOps.contain(image, (tile_width - 12, tile_height - 34))
                sheet.paste(thumb, (x + (tile_width - thumb.width) // 2, y + 5))
        except (OSError, UnidentifiedImageError):
            draw.text((x + 8, y + 10), "UNREADABLE", fill="red", font=font)
        draw.text((x + 8, y + tile_height - 25), path.name, fill="black", font=font)
        draw.rectangle((x, y, x + tile_width - 1, y + tile_height - 1), outline="#bbbbbb")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def audit(images_dir: Path, output_dir: Path, representative_count: int = 24) -> dict:
    paths = sorted((p for p in images_dir.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS),
                   key=lambda p: p.name)
    if not paths:
        raise ValueError("no JPEG/PNG files found")
    valid = []
    bad = []
    dimensions = Counter()
    hashes = defaultdict(list)
    features = {}
    for path in paths:
        try:
            data = path.read_bytes()
            with Image.open(path) as source:
                source.verify()
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                dimensions[f"{image.width}x{image.height}"] += 1
                features[path] = _features(image)
            hashes[hashlib.sha256(data).hexdigest()].append(path.name)
            valid.append(path)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            bad.append({"file": path.name, "reason": str(exc)})
    duplicates = [group for group in hashes.values() if len(group) > 1]
    near = []
    for i, first in enumerate(valid):
        av, ah = features[first]
        for second in valid[i + 1:]:
            bv, bh = features[second]
            if (ah ^ bh).bit_count() <= 3:
                mae = _distance(av, bv)
                if mae <= 6:
                    near.append({"a": first.name, "b": second.name, "thumbnail_mae": round(mae, 2)})
    selected = []
    if valid:
        for path in (valid[0], valid[len(valid) // 2], valid[-1]):
            if path not in selected:
                selected.append(path)
    while len(selected) < min(representative_count, len(valid)):
        remaining = (p for p in valid if p not in selected)
        choice = max(remaining, key=lambda p: min(_distance(features[p][0], features[q][0]) for q in selected))
        selected.append(choice)
    selected.sort(key=lambda p: p.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    contact_sheet(selected, output_dir / "代表性样本联系表.png")
    for page, start in enumerate(range(0, len(paths), 48), 1):
        contact_sheet(paths[start:start + 48], output_dir / f"全量索引_{page:02d}.png",
                      columns=4, tile_width=240, tile_height=190)
    (output_dir / "代表性样本文件名.txt").write_text("\n".join(p.name for p in selected) + "\n", encoding="utf-8")
    with (output_dir / "分组与划分模板.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("filename", "capture_session", "scene_group", "split", "annotation_reviewed"))
        writer.writerows((p.name, "", "", "", "") for p in valid)
    result = {"source": str(images_dir), "image_count": len(paths), "valid_count": len(valid),
              "invalid": bad, "dimensions": dict(dimensions), "exact_duplicate_groups": duplicates,
              "near_duplicate_candidate_count": len(near),
              "near_duplicate_candidates_first_50": near[:50],
              "representative_files": [p.name for p in selected],
              "warning": "near duplicates are visual candidates, not ground truth; source images are not an independent test set"}
    (output_dir / "数据检查.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.images, args.output)
    print(json.dumps({key: report[key] for key in ("image_count", "valid_count", "invalid",
                     "dimensions", "exact_duplicate_groups", "near_duplicate_candidate_count",
                     "representative_files")}, ensure_ascii=False, indent=2))
