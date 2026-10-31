"""Build a versioned human-only formal dataset; reject changed sources and unresolved alerts."""
from __future__ import annotations
import argparse,hashlib,json,shutil
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

CLASSES=("block_blue","block_green","block_red","block_yellow")
EXCLUDED={"321.jpg":"tiny fifth green box on background; human repair needed",
          "399.jpg":"tiny fourth yellow box on background; human repair needed",
          "439.jpg":"tiny fourth red box on background; human repair needed"}
KNOWN_PAIRS=(("195.jpg","204.jpg"),("197.jpg","223.jpg"),("198.jpg","246.jpg"),
             ("223.jpg","231.jpg"),("302.jpg","303.jpg"),("304.jpg","305.jpg"),
             ("350.jpg","351.jpg"),("354.jpg","355.jpg"))


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def scene_proxy(name):
    n=int(Path(name).stem)
    for lo,hi in ((193,251),(263,315),(316,369),(370,424),(425,440)):
        if lo<=n<=hi:return f"numbered_scene_proxy_{lo}_{hi}"
    raise ValueError(f"unexpected filename {name}")


def build(audit_path:Path,trial:Path,manual:Path,source:Path,output:Path):
    audit=json.loads(audit_path.read_text(encoding="utf-8"))
    if output.exists():raise FileExistsError(f"refusing to overwrite {output}")
    if audit["blocking_problems"] or audit["empty_labels"] or len(audit["records"])!=206:
        raise ValueError("206-image audit has blocking problems or empty labels")
    alerts=audit["visual_review_alerts"]
    if {x["image"] for x in alerts}!=set(EXCLUDED) or any(x["type"]!="unusual_area" for x in alerts):
        raise ValueError(f"visual alerts changed, inspect before building: {alerts}")
    records={r["image"]:r for r in audit["records"]}
    selected={}
    for name,rec in records.items():
        image=source/name
        labels=trial/"labels" if rec["label_source"]=="trial20" else manual/"reviewed_labels"
        label=labels/(Path(name).stem+".txt")
        if sha(image)!=rec["source_sha256"] or sha(label)!=rec["label_sha256"]:
            raise ValueError(f"source image or human label changed after audit: {name}")
        if name in EXCLUDED:continue
        group=scene_proxy(name)
        split="val" if group in ("numbered_scene_proxy_370_424","numbered_scene_proxy_425_440") else "train"
        selected[name]={"split":split,"scene_proxy":group,"source_image":str(image.resolve()),
                        "source_label":str(label.resolve()),"image_sha256":rec["source_sha256"],
                        "label_sha256":rec["label_sha256"],"box_counts":dict(Counter(b["raw_label"] for b in rec["boxes"]))}
    if len(selected)!=203:raise ValueError("expected 203 eligible images after 3 excluded")
    for a,b in KNOWN_PAIRS:
        if a in selected and b in selected and selected[a]["split"]!=selected[b]["split"]:
            raise ValueError(f"known near-duplicate candidate across train/val: {a},{b}")
    counts={"train":Counter(),"val":Counter()}
    images={"train":0,"val":0}
    for r in selected.values():
        split=r["split"];images[split]+=1;counts[split].update(r["box_counts"])
    if any(counts[split][cls]<1 for split in images for cls in CLASSES):
        raise ValueError("one split lacks a class")
    for split in ("train","val"):
        (output/"images"/split).mkdir(parents=True)
        (output/"labels"/split).mkdir(parents=True)
    for name,r in sorted(selected.items(),key=lambda kv:int(Path(kv[0]).stem)):
        split=r["split"]
        dst_image=output/"images"/split/name
        dst_label=output/"labels"/split/(Path(name).stem+".txt")
        shutil.copy2(r["source_image"],dst_image);shutil.copy2(r["source_label"],dst_label)
        if sha(dst_image)!=r["image_sha256"] or sha(dst_label)!=r["label_sha256"]:
            raise IOError(f"copied file mismatch: {name}")
    yaml=f'path: "{output.resolve().as_posix()}"\ntrain: images/train\nval: images/val\nnames:\n'
    yaml+="".join(f"  {i}: {cls}\n" for i,cls in enumerate(CLASSES))
    (output/"data.yaml").write_text(yaml,encoding="utf-8")
    manifest={"purpose":"YOLOv8n formal baseline training/validation, NOT independent test",
              "created_at":datetime.now(timezone.utc).isoformat(),
              "source_audit":str(audit_path.resolve()),"source_audit_sha256":sha(audit_path),
              "classes":CLASSES,"image_size":[640,480],
              "assumption":"actual capture sessions unknown; contiguous numbered scene proxies used conservatively. 370-440 held out as validation; all data originate from the same old collection.",
              "val_group_policy":["numbered_scene_proxy_370_424","numbered_scene_proxy_425_440"],
              "known_near_duplicate_candidate_pairs":KNOWN_PAIRS,
              "excluded_unrepaired":EXCLUDED,"source_images":206,
              "selected_images":len(selected),"image_counts":images,
              "class_box_counts":{split:{c:counts[split][c] for c in CLASSES} for split in images},
              "files":selected,
              "warning":"machine prelabel_pilot10 suggestions are not included; no independent test or verified empty scenes"}
    (output/"split_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    summary={k:manifest[k] for k in ("source_images","selected_images","image_counts","class_box_counts","excluded_unrepaired","assumption")}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return manifest


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--audit",type=Path,default=Path("data/a_blocks/formal_audit_20261022_01/audit.json"))
    p.add_argument("--trial",type=Path,default=Path("data/a_blocks/trial20"))
    p.add_argument("--manual",type=Path,default=Path("data/a_blocks/manual186"))
    p.add_argument("--source",type=Path,default=Path("data/a_blocks/annotation_work"))
    p.add_argument("--output",type=Path,default=Path("data/a_blocks/formal_v1_20261022"))
    a=p.parse_args()
    build(a.audit,a.trial,a.manual,a.source,a.output)
