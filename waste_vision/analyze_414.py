"""Compare 414.jpg's manually reviewed YOLO labels with actual CPU-model predictions."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw

CLASSES=("block_blue","block_green","block_red","block_yellow")
COLORS=("#1687ee","#238a39","#e33831","#c5a000")


def iou(a,b):
    x1,y1=max(a[0],b[0]),max(a[1],b[1])
    x2,y2=min(a[2],b[2]),min(a[3],b[3])
    inter=max(0,x2-x1)*max(0,y2-y1)
    area_a=(a[2]-a[0])*(a[3]-a[1])
    area_b=(b[2]-b[0])*(b[3]-b[1])
    return inter/(area_a+area_b-inter) if area_a+area_b-inter else 0.0


def run(image_path,gt_path,pred_path,output):
    with Image.open(image_path) as im:
        im.load(); base=im.convert("RGB")
    w,h=base.size
    if (w,h)!=(640,480): raise ValueError(f"unexpected image size {w}x{h}")
    labels=[]
    for line in gt_path.read_text(encoding="utf-8").splitlines():
        cls_s,cx_s,cy_s,bw_s,bh_s=line.split()
        cls=int(cls_s); cx,cy,bw,bh=map(float,(cx_s,cy_s,bw_s,bh_s))
        box=[(cx-bw/2)*w,(cy-bh/2)*h,(cx+bw/2)*w,(cy+bh/2)*h]
        labels.append({"class_id":cls,"raw_label":CLASSES[cls],"bbox_xyxy_raw_camera":box})
    dataset=json.loads(pred_path.read_text(encoding="utf-8"))
    rec=next(x for x in dataset["images"] if x["image"]==image_path.name)
    if rec["coordinate_space"]!="raw_camera" or (rec["width"],rec["height"])!=(w,h):
        raise ValueError("prediction coordinate-space or size mismatch")
    preds=rec["boxes"]
    for p in preds:
        box=p["bbox_xyxy_raw_camera"]
        if not (0<=box[0]<box[2]<=w and 0<=box[1]<box[3]<=h):
            raise ValueError(f"invalid predicted raw-coordinate box: {box}")
    gt_im,pred_im=base.copy(),base.copy()
    for canvas,records,title in ((gt_im,labels,"414.jpg - HUMAN GT (4)"),
                                 (pred_im,preds,f"414.jpg - MODEL PRED ({len(preds)})")):
        draw=ImageDraw.Draw(canvas)
        draw.rectangle((0,0,w-1,23),fill="white")
        draw.text((5,5),title,fill="black")
        for idx,rec in enumerate(records,1):
            cls=rec["class_id"]; box=rec["bbox_xyxy_raw_camera"]
            draw.rectangle(box,outline=COLORS[cls],width=3)
            caption=f"{idx}:{rec['raw_label']}"
            if "confidence" in rec: caption+=f" {rec['confidence']:.2f}"
            draw.text((box[0]+3,max(25,box[1]-13)),caption,fill=COLORS[cls])
    side=Image.new("RGB",(2*w,h),"white")
    side.paste(gt_im,(0,0)); side.paste(pred_im,(w,0))
    output.mkdir(parents=True,exist_ok=True)
    side.save(output/"414_gt_vs_prediction.png")
    rows=[]
    for i,p in enumerate(preds,1):
        overlaps=sorted(({"gt_index":j,"gt_label":g["raw_label"],"iou":iou(p["bbox_xyxy_raw_camera"],g["bbox_xyxy_raw_camera"])}
                         for j,g in enumerate(labels,1)),key=lambda z:z["iou"],reverse=True)
        rows.append({"prediction_index":i,"raw_label":p["raw_label"],"confidence":p["confidence"],
                     "bbox_xyxy_raw_camera":p["bbox_xyxy_raw_camera"],"closest_gt":overlaps[0]})
    pairs=[]
    for i,p in enumerate(preds):
        for j,q in enumerate(preds[i+1:],i+1):
            score=iou(p["bbox_xyxy_raw_camera"],q["bbox_xyxy_raw_camera"])
            if score>=0.5:
                pairs.append({"prediction_pair":[i+1,j+1],"iou":score,
                              "type":"same_class" if p["class_id"]==q["class_id"] else "different_class",
                              "labels":[p["raw_label"],q["raw_label"]]})
    result={"image":"414.jpg","coordinate_space":"raw_camera","gt_count":len(labels),"prediction_count":len(preds),
            "confidence":dataset["conf"],"nms_iou":dataset["iou"],"ground_truth":labels,
            "predictions":rows,"prediction_overlap_pairs_iou_ge_0_5":pairs,
            "note":"IoU is diagnostic; these predictions are not reviewed labels."}
    (output/"414_analysis.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"gt_count":len(labels),"prediction_count":len(preds),"overlap_pairs":pairs,
                      "predictions":rows},ensure_ascii=False,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--image",type=Path,default=Path("data/a_blocks/trial20/images/414.jpg"))
    parser.add_argument("--gt",type=Path,default=Path("data/a_blocks/trial20/labels/414.txt"))
    parser.add_argument("--pred",type=Path,default=Path("data/a_blocks/runs/yolov8n_smoke_cpu_20261022_01/val_predictions_conf025/predictions.json"))
    parser.add_argument("--output",type=Path,default=Path("docs/a/数据审查"))
    args=parser.parse_args()
    run(args.image,args.gt,args.pred,args.output)
