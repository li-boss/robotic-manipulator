"""Audit the 206 human YOLO labels against immutable image manifests and render all overlays."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw

CLASSES=("block_blue","block_green","block_red","block_yellow")
COLORS=("#1288ef","#238a3a","#e23833","#c89b00")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iou(a,b):
    x1,y1=max(a[0],b[0]),max(a[1],b[1])
    x2,y2=min(a[2],b[2]),min(a[3],b[3])
    inter=max(0,x2-x1)*max(0,y2-y1)
    area_a=(a[2]-a[0])*(a[3]-a[1])
    area_b=(b[2]-b[0])*(b[3]-b[1])
    return inter/(area_a+area_b-inter) if area_a+area_b-inter else 0


def audit(source: Path, trial: Path, manual: Path, classes: Path, output: Path) -> dict:
    names=tuple(s.strip() for s in classes.read_text(encoding="utf-8-sig").splitlines() if s.strip())
    if names!=CLASSES: raise ValueError(f"class order changed: {names}")
    source_manifest=json.loads((source/"source_manifest.json").read_text(encoding="utf-8"))
    source_hashes={r["file"]:r["sha256"] for r in source_manifest["files"]}
    trial_manifest=json.loads((trial/"manifest.json").read_text(encoding="utf-8"))
    manual_manifest=json.loads((manual/"manifest.json").read_text(encoding="utf-8"))
    trial_hashes={r["filename"]:r["sha256"] for r in trial_manifest["images"]}
    trial_names=set(trial_hashes)
    manual_names={r["filename"] for r in manual_manifest["images"]}
    problems=[]
    if tuple(trial_manifest["classes"])!=CLASSES or tuple(manual_manifest["classes"])!=CLASSES:
        problems.append({"type":"class_order","detail":"manifest class order mismatch"})
    if trial_names & manual_names or trial_names | manual_names != set(source_hashes):
        problems.append({"type":"image_set","detail":"20+186 names do not partition source manifest"})
    source_files={p.name for p in source.glob("*.jpg")}
    if source_files!=set(source_hashes):
        problems.append({"type":"image_set","detail":"source directory differs from source manifest"})
    if sorted(p.name for p in (trial/"labels").glob("*.txt"))!=sorted(Path(n).stem+".txt" for n in trial_names):
        problems.append({"type":"label_set","detail":"trial label names differ from 20 image names"})
    if sorted(p.name for p in (manual/"reviewed_labels").glob("*.txt"))!=sorted(Path(n).stem+".txt" for n in manual_names):
        problems.append({"type":"label_set","detail":"manual label names differ from 186 image names"})
    for marker in (trial/"pending_review.json",manual/"needs_recheck.json"):
        if marker.exists():
            waiting=json.loads(marker.read_text(encoding="utf-8"))
            if waiting: problems.append({"type":"pending_review","file":str(marker),"items":waiting})
    if len(source_hashes)!=206 or len(trial_names)!=20 or len(manual_names)!=186:
        problems.append({"type":"count","detail":"expected 206/20/186"})
    records=[]
    counts=Counter()
    empty=[]
    alerts=[]
    output.mkdir(parents=True,exist_ok=False)
    (output/"contact_pages").mkdir()
    (output/"overlays").mkdir()
    for name in sorted(source_hashes,key=lambda n:int(Path(n).stem)):
        image=source/name
        label=(trial/"labels" if name in trial_names else manual/"reviewed_labels")/(Path(name).stem+".txt")
        if not image.is_file() or not label.is_file():
            problems.append({"type":"missing_file","image":name})
            continue
        if sha(image)!=source_hashes[name]:
            problems.append({"type":"image_hash","image":name})
        if name in trial_names and sha(trial/"images"/name)!=trial_hashes[name]:
            problems.append({"type":"trial_copy_hash","image":name})
        with Image.open(image) as im:
            im.load()
            w,h=im.size
            canvas=im.convert("RGB")
        if (w,h)!=(640,480):
            problems.append({"type":"image_size","image":name,"size":[w,h]})
            continue
        boxes=[]
        content=label.read_text(encoding="utf-8-sig").strip()
        if not content:
            empty.append(name)
        else:
            for line_no,line in enumerate(content.splitlines(),1):
                try:
                    items=line.split()
                    if len(items)!=5: raise ValueError("expected class cx cy w h")
                    cls=int(items[0])
                    if cls not in range(4): raise ValueError("class outside 0..3")
                    cx,cy,bw,bh=map(float,items[1:])
                    from math import isfinite
                    if not all(map(isfinite,(cx,cy,bw,bh))) or not (
                        0<bw<=1 and 0<bh<=1 and
                        -0.000001<=cx-bw/2<cx+bw/2<=1.000001 and
                        -0.000001<=cy-bh/2<cy+bh/2<=1.000001):
                        raise ValueError("invalid normalized coordinates")
                    box=[(cx-bw/2)*w,(cy-bh/2)*h,(cx+bw/2)*w,(cy+bh/2)*h]
                    boxes.append({"class_id":cls,"raw_label":CLASSES[cls],
                                  "bbox_xyxy_raw_camera":box,"line":line_no})
                    counts[CLASSES[cls]]+=1
                    area=bw*bh
                    if area<0.02 or area>0.45:
                        alerts.append({"type":"unusual_area","image":name,"line":line_no,"area_fraction":round(area,4)})
                except (ValueError,OverflowError) as exc:
                    problems.append({"type":"invalid_label","image":name,"line":line_no,"error":str(exc)})
        for i,a in enumerate(boxes):
            for j,b in enumerate(boxes[i+1:],i+1):
                score=iou(a["bbox_xyxy_raw_camera"],b["bbox_xyxy_raw_camera"])
                if score>=0.90:
                    alerts.append({"type":"near_identical_boxes","image":name,"lines":[i+1,j+1],
                                   "classes":[a["raw_label"],b["raw_label"]],"iou":round(score,4)})
        draw=ImageDraw.Draw(canvas)
        for j,box in enumerate(boxes,1):
            cls=box["class_id"]; x1,y1,x2,y2=box["bbox_xyxy_raw_camera"]
            draw.rectangle((x1,y1,x2,y2),outline=COLORS[cls],width=3)
            draw.text((x1+3,max(18,y1-14)),f"{j}:{CLASSES[cls]}",fill=COLORS[cls])
        draw.rectangle((0,0,w-1,19),fill="white")
        draw.text((5,3),f"{name}  HUMAN {len(boxes)} BOXES",fill="black")
        canvas.save(output/"overlays"/f"{Path(name).stem}.png")
        records.append({"image":name,"source_sha256":source_hashes[name],
                        "label_source":"trial20" if name in trial_names else "manual186",
                        "label_sha256":sha(label),"boxes":boxes})
    for page_start in range(0,len(records),16):
        page=Image.new("RGB",(1280,960),"white")
        for j,rec in enumerate(records[page_start:page_start+16]):
            with Image.open(output/"overlays"/f"{Path(rec['image']).stem}.png") as im:
                thumb=im.resize((320,240))
                page.paste(thumb,((j%4)*320,(j//4)*240))
        page.save(output/"contact_pages"/f"page_{page_start//16+1:02d}.jpg",quality=92)
    result={"created_at":datetime.now(timezone.utc).isoformat(),"purpose":"human-label format and visual review, not accuracy",
            "source_images":len(source_hashes),"trial_images":len(trial_names),"manual_images":len(manual_names),
            "labeled_images":len(records),"empty_labels":empty,"class_box_counts":{c:counts[c] for c in CLASSES},
            "total_boxes":sum(counts.values()),"blocking_problems":problems,"visual_review_alerts":alerts,
            "contact_pages":len(list((output/"contact_pages").glob("*.jpg"))),
            "warning":"contact sheets need human visual inspection; no empty label is accepted without confirmation",
            "records":records}
    (output/"audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("source_images","trial_images","manual_images","labeled_images",
            "empty_labels","class_box_counts","total_boxes","blocking_problems",
            "visual_review_alerts","contact_pages")},ensure_ascii=False,indent=2))
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,default=Path("data/a_blocks/annotation_work"))
    p.add_argument("--trial",type=Path,default=Path("data/a_blocks/trial20"))
    p.add_argument("--manual",type=Path,default=Path("data/a_blocks/manual186"))
    p.add_argument("--classes",type=Path,default=Path("configs/a_confirmed_classes.txt"))
    p.add_argument("--output",type=Path,default=Path("data/a_blocks/formal_audit_20261022_01"))
    a=p.parse_args()
    audit(a.source,a.trial,a.manual,a.classes,a.output)
