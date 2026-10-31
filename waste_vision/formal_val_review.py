"""Validate completed formal weights on the 60 held-out old-batch images and save overlays."""
from __future__ import annotations
import argparse,csv,hashlib,json,statistics,time
from datetime import datetime,timezone
from pathlib import Path
import os
from PIL import Image,ImageDraw
os.environ.setdefault("YOLO_CONFIG_DIR",str(Path("data/a_blocks/cache/ultralytics").resolve()))
from ultralytics import YOLO

CLASSES=("block_blue","block_green","block_red","block_yellow")
COLORS=("#1687ee","#238a39","#e33831","#c5a000")


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def iou(a,b):
    x1,y1=max(a[0],b[0]),max(a[1],b[1]);x2,y2=min(a[2],b[2]),min(a[3],b[3])
    area=max(0,x2-x1)*max(0,y2-y1)
    aa=(a[2]-a[0])*(a[3]-a[1]);bb=(b[2]-b[0])*(b[3]-b[1])
    return area/(aa+bb-area) if aa+bb-area else 0.0


def gt_boxes(path,w,h):
    rows=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        cls,cx,cy,bw,bh=line.split();cls=int(cls);cx,cy,bw,bh=map(float,(cx,cy,bw,bh))
        rows.append({"class_id":cls,"raw_label":CLASSES[cls],
                     "bbox_xyxy_raw_camera":[(cx-bw/2)*w,(cy-bh/2)*h,(cx+bw/2)*w,(cy+bh/2)*h]})
    return rows


def count_errors(gt,pred):
    matched=set();false_positive=[];wrong_class=[]
    for i,p in enumerate(pred):
        overlaps=sorted(((iou(p["bbox_xyxy_raw_camera"],g["bbox_xyxy_raw_camera"]),j,g)
                         for j,g in enumerate(gt)),reverse=True,key=lambda x:x[0])
        if overlaps and overlaps[0][0]>=0.5 and overlaps[0][2]["class_id"]!=p["class_id"]:
            wrong_class.append(i+1)
        correct=next(((score,j) for score,j,g in overlaps if score>=0.5 and g["class_id"]==p["class_id"] and j not in matched),None)
        if correct:matched.add(correct[1])
        else:false_positive.append(i+1)
    cross=[];same=[]
    for i,p in enumerate(pred):
        for j,q in enumerate(pred[i+1:],i+1):
            if iou(p["bbox_xyxy_raw_camera"],q["bbox_xyxy_raw_camera"])>=0.5:
                (same if p["class_id"]==q["class_id"] else cross).append([i+1,j+1])
    return {"matched":len(matched),"missed":len(gt)-len(matched),"false_positive_indices":false_positive,
            "wrong_class_indices":wrong_class,"same_class_overlap_pairs":same,"cross_class_overlap_pairs":cross}


def timing_summary(values):
    ordered=sorted(float(x) for x in values)
    if not ordered:return {"samples":0,"mean":None,"p95":None}
    rank=0.95*(len(ordered)-1);lo=int(rank);hi=min(lo+1,len(ordered)-1)
    return {"samples":len(ordered),"mean":statistics.mean(ordered),
            "p95":ordered[lo]+(ordered[hi]-ordered[lo])*(rank-lo)}


def run(data:Path,train_run:Path,output:Path,device:str,conf:float,nms_iou:float):
    train=json.loads((train_run/"formal_train_result.json").read_text(encoding="utf-8"))
    if train["status"]!="completed":raise ValueError("formal training has not completed")
    manifest=json.loads((data/"split_manifest.json").read_text(encoding="utf-8"))
    if train["data_manifest_sha256"]!=sha(data/"split_manifest.json"):raise ValueError("data manifest changed since training")
    best=train_run/"weights"/"best.pt"
    if sha(best)!=train["best_sha256"]:raise ValueError("best.pt changed")
    if output.exists():raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    (output/"overlays").mkdir()
    model=YOLO(str(best))
    names=tuple(model.names[i] for i in range(4))
    if names!=CLASSES:raise ValueError(f"model class order {names}")
    # Ultralytics metrics use the validation split; no independent test is present.
    metrics=model.val(data=str((data/"data.yaml").resolve()),split="val",imgsz=640,
                      device=device,batch=8,workers=0,conf=0.001,iou=0.7,
                      plots=True,project=str(output.resolve()),name="ultralytics_val",exist_ok=False,verbose=False)
    b=metrics.box
    ap_indices=[int(x) for x in b.ap_class_index]
    per_class={}
    for pos,cls in enumerate(ap_indices):
        per_class[CLASSES[cls]]={"precision":float(b.p[pos]),"recall":float(b.r[pos]),
                                "ap50":float(b.ap50[pos])}
    for cls in CLASSES:per_class.setdefault(cls,{"precision":None,"recall":None,"ap50":None})
    recs=[];speeds=[];wall_ms=[]
    files=sorted((data/"images"/"val").glob("*.jpg"),key=lambda p:int(p.stem))
    if len(files)!=manifest["image_counts"]["val"]:raise ValueError("validation image count changed")
    thumbs=[]
    (output/"gt_vs_prediction").mkdir()
    for path in files:
        started=time.perf_counter()
        result=model.predict(source=str(path),imgsz=640,conf=conf,iou=nms_iou,
                             device=device,verbose=False)[0]
        predict_wall_ms=(time.perf_counter()-started)*1000
        with Image.open(path) as image:
            image.load();canvas=image.convert("RGB")
        w,h=canvas.size
        if result.orig_shape!=(h,w):raise ValueError(f"raw coordinate mismatch: {path.name}")
        pred=[];draw=ImageDraw.Draw(canvas)
        for box in result.boxes:
            cls=int(box.cls.item());xyxy=[float(x) for x in box.xyxy[0].tolist()]
            if cls not in range(4) or not (0<=xyxy[0]<xyxy[2]<=w and 0<=xyxy[1]<xyxy[3]<=h):
                raise ValueError(f"invalid raw-coordinate prediction: {path.name}")
            row={"class_id":cls,"raw_label":CLASSES[cls],"confidence":float(box.conf.item()),
                 "bbox_xyxy_raw_camera":xyxy,
                 "center_x":(xyxy[0]+xyxy[2])/2,"center_y":(xyxy[1]+xyxy[3])/2}
            pred.append(row)
            draw.rectangle(xyxy,outline=COLORS[cls],width=3)
            draw.text((xyxy[0]+2,max(22,xyxy[1]-14)),f"{CLASSES[cls]} {row['confidence']:.2f}",fill=COLORS[cls])
        gt=gt_boxes(data/"labels"/"val"/(path.stem+".txt"),w,h)
        gt_canvas=Image.open(path).convert("RGB")
        gt_draw=ImageDraw.Draw(gt_canvas)
        for item in gt:
            gt_draw.rectangle(item["bbox_xyxy_raw_camera"],outline=COLORS[item["class_id"]],width=3)
            x1,y1,*_=item["bbox_xyxy_raw_camera"]
            gt_draw.text((x1+2,max(22,y1-14)),item["raw_label"],fill=COLORS[item["class_id"]])
        gt_draw.rectangle((0,0,w-1,20),fill="white")
        gt_draw.text((4,3),f"{path.name}: HUMAN GT {len(gt)}",fill="black")
        draw.rectangle((0,0,w-1,20),fill="white")
        draw.text((4,3),f"{path.name}: GT {len(gt)} PRED {len(pred)}",fill="black")
        canvas.save(output/"overlays"/f"{path.stem}.png")
        comparison=Image.new("RGB",(w*2,h),"white")
        comparison.paste(gt_canvas,(0,0));comparison.paste(canvas,(w,0))
        comparison.save(output/"gt_vs_prediction"/f"{path.stem}.png")
        thumbs.append(canvas.resize((320,240)))
        errors=count_errors(gt,pred)
        recs.append({"image":path.name,"gt":gt,"predictions":pred,"diagnostics_conf025_iou05":errors,
                     "model_stage_ms":result.speed,"direct_predict_wall_ms":predict_wall_ms})
        speeds.append(result.speed);wall_ms.append(predict_wall_ms)
    (output/"contact_pages").mkdir()
    for start in range(0,len(thumbs),16):
        sheet=Image.new("RGB",(1280,960),"white")
        for j,im in enumerate(thumbs[start:start+16]):sheet.paste(im,((j%4)*320,(j//4)*240))
        sheet.save(output/"contact_pages"/f"page_{start//16+1:02d}.jpg",quality=92)
    result={"purpose":"validation-set metrics and exploratory threshold review; NOT independent test",
            "created_at":datetime.now(timezone.utc).isoformat(),"weights":str(best.resolve()),"weights_sha256":sha(best),
            "device":device,"data":str(data.resolve()),"val_images":len(files),
            "metric_settings":{"conf":0.001,"nms_iou":0.7,"imgsz":640},
            "review_settings":{"conf":conf,"nms_iou":nms_iou,"coordinate_space":"raw_camera"},
            "mAP50":float(b.map50),"mAP50_95":float(b.map),"per_class":per_class,
            "timing_after_val_warmup_cpu_ms":{
                "preprocess":timing_summary([x["preprocess"] for x in speeds]),
                "inference":timing_summary([x["inference"] for x in speeds]),
                "postprocess":timing_summary([x["postprocess"] for x in speeds]),
                "direct_predict_wall":timing_summary(wall_ms)},
            "warning":"old same-collection validation only; no empty scenes or independent test; no Atlas/grasp validation",
            "images":recs}
    (output/"validation_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("val_images","mAP50","mAP50_95","per_class","review_settings")},
                     ensure_ascii=False,indent=2))
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--data",type=Path,default=Path("data/a_blocks/formal_v1_20261022"))
    p.add_argument("--train-run",type=Path,default=Path("data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01"))
    p.add_argument("--output",type=Path,default=Path("data/a_blocks/evaluations/yolov8n_formal_v1_cpu_val_20261022_01"))
    p.add_argument("--device",default="cpu")
    p.add_argument("--conf",type=float,default=0.25)
    p.add_argument("--iou",type=float,default=0.5)
    a=p.parse_args()
    run(a.data,a.train_run,a.output,a.device,a.conf,a.iou)
