"""Count only human-reviewed YOLO labels; pending model suggestions are never counted."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

CLASSES=("block_blue","block_green","block_red","block_yellow")


def count_label(path: Path) -> Counter:
    counts=Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        vals=line.split()
        if len(vals)!=5 or int(vals[0]) not in range(4):
            raise ValueError(f"invalid label format: {path}")
        from math import isfinite
        cx, cy, bw, bh = map(float, vals[1:])
        if not all(map(isfinite, (cx, cy, bw, bh))) or not (0 < bw <= 1 and 0 < bh <= 1
                and 0 <= cx-bw/2 < cx+bw/2 <= 1 and 0 <= cy-bh/2 < cy+bh/2 <= 1):
            raise ValueError(f"invalid normalized box: {path}")
        counts[CLASSES[int(vals[0])]]+=1
    return counts


def run(trial: Path, manual: Path, output: Path) -> dict:
    manifest=json.loads((manual/"manifest.json").read_text(encoding="utf-8"))
    names=[rec["filename"] for rec in manifest["images"]]
    if len(names)!=186:
        raise ValueError(f"expected 186 remaining images, got {len(names)}")
    trial_files=sorted((trial/"labels").glob("*.txt"))
    if len(trial_files)!=20:
        raise ValueError(f"expected 20 trial labels, got {len(trial_files)}")
    counts20=Counter()
    for p in trial_files: counts20.update(count_label(p))
    reviewed_dir=Path(manifest["reviewed_labels_dir"])
    reviewed_files=list(reviewed_dir.glob("*.txt"))
    if any(p.stem+".jpg" not in names for p in reviewed_files):
        raise ValueError("reviewed label outside remaining-186 manifest")
    counts186=Counter()
    rows=[]
    for name in names:
        path=reviewed_dir/(Path(name).stem+".txt")
        if path.exists():
            counts=count_label(path)
            counts186.update(counts)
            status="human_reviewed"
            per_class=[counts.get(cls,0) for cls in CLASSES]
        else:
            status="pending"
            per_class=[""]*4
        num=int(Path(name).stem)
        sequence=("193-251" if 193<=num<=251 else
                  "263-315" if 263<=num<=315 else
                  "316-369" if 316<=num<=369 else
                  "370-424" if 370<=num<=424 else
                  "425-440" if 425<=num<=440 else "other")
        rows.append([name,status,sequence,"capture/scene group to confirm",""]+per_class)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.writer(f)
        writer.writerow(["filename","human_review_status","rough_sequence_for_review_only","capture_scene_group","split",
                         *CLASSES])
        writer.writerows(rows)
    result={"trial20":{"human_reviewed_images":20,"counts":{c:counts20[c] for c in CLASSES}},
            "remaining186":{"total":186,"human_reviewed_images":len(reviewed_files),
                            "pending_images":186-len(reviewed_files),
                            "confirmed_counts":{c:(counts186[c] if reviewed_files else None) for c in CLASSES}},
            "warning":"blank per-class cells mean UNANNOTATED/UNKNOWN, not zero objects; rough sequences are not final data splits",
            "csv":str(output)}
    (output.with_suffix(".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--trial",type=Path,default=Path("data/a_blocks/trial20"))
    p.add_argument("--manual",type=Path,default=Path("data/a_blocks/manual186"))
    p.add_argument("--output",type=Path,default=Path("docs/a/数据审查/剩余186标注进度.csv"))
    args=p.parse_args()
    print(json.dumps(run(args.trial,args.manual,args.output),ensure_ascii=False,indent=2))
