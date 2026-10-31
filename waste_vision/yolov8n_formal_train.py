"""Train the audited formal YOLOv8n baseline without altering the CPU smoke run."""
from __future__ import annotations
import argparse,csv,hashlib,json,os,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from waste_vision.label_check import check

CLASSES=("block_blue","block_green","block_red","block_yellow")


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def verify_data(data:Path,classes:Path):
    manifest=json.loads((data/"split_manifest.json").read_text(encoding="utf-8"))
    expected=tuple(s.strip() for s in classes.read_text(encoding="utf-8-sig").splitlines() if s.strip())
    if expected!=CLASSES or tuple(manifest["classes"])!=CLASSES or manifest["selected_images"]!=203:
        raise ValueError("class order or formal dataset manifest changed")
    if set(manifest["excluded_unrepaired"])!={"321.jpg","399.jpg","439.jpg"}:
        raise ValueError("excluded unrepaired images changed")
    checks={}
    for split in ("train","val"):
        result=check(data/"images"/split,data/"labels"/split,classes)
        if not result["ready_for_training"] or result["images"]!=manifest["image_counts"][split]:
            raise ValueError(f"{split} data invalid: {result}")
        if result["class_box_counts"]!=manifest["class_box_counts"][split]:
            raise ValueError(f"{split} class count mismatch")
        checks[split]=result
    for name,r in manifest["files"].items():
        part=r["split"]
        if sha(data/"images"/part/name)!=r["image_sha256"] or sha(data/"labels"/part/(Path(name).stem+".txt"))!=r["label_sha256"]:
            raise ValueError(f"formal copy changed: {name}")
    if manifest["files"]["414.jpg"]["split"]!="val":
        raise ValueError("414.jpg split changed")
    return manifest,checks


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data",type=Path,default=Path("data/a_blocks/formal_v1_20261022"))
    p.add_argument("--classes",type=Path,default=Path("configs/a_confirmed_classes.txt"))
    p.add_argument("--pretrained",type=Path,default=Path("yolov8n.pt"))
    p.add_argument("--output",type=Path,default=Path("data/a_blocks/runs/yolov8n_formal_v1_gpu_20261022_01"))
    p.add_argument("--device",choices=("0","cpu"),default="0")
    p.add_argument("--batch",type=int,default=8)
    p.add_argument("--epochs",type=int,default=100)
    p.add_argument("--patience",type=int,default=20)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(f"refusing to overwrite run: {args.output}")
    if args.epochs<1 or args.epochs>100 or args.patience<1 or args.batch<1:raise ValueError("invalid training limits")
    if not args.pretrained.is_file():raise FileNotFoundError(f"official pretrained weight missing: {args.pretrained}")
    manifest,checks=verify_data(args.data,args.classes)
    os.environ.setdefault("YOLO_CONFIG_DIR",str((Path("data/a_blocks/cache/ultralytics")).resolve()))
    import torch,ultralytics
    from ultralytics import YOLO
    if args.device=="0":
        if not torch.cuda.is_available():raise RuntimeError("CUDA not available in this interpreter")
        x=torch.ones((256,256),device="cuda")
        y=x@x
        torch.cuda.synchronize()
        if float(y[0,0])!=256:raise RuntimeError("CUDA tensor operation failed")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.mkdir()
    info={"status":"running","started_at":now(),"purpose":"formal YOLOv8n baseline train/val; NOT independent test",
          "data":str(args.data.resolve()),"data_manifest_sha256":sha(args.data/"split_manifest.json"),
          "pretrained":str(args.pretrained.resolve()),"pretrained_sha256":sha(args.pretrained),
          "class_order":CLASSES,"image_counts":manifest["image_counts"],
          "class_box_counts":manifest["class_box_counts"],
          "excluded_unrepaired":manifest["excluded_unrepaired"],
          "python":sys.version,"interpreter":sys.executable,"torch":torch.__version__,
          "torch_cuda_runtime":torch.version.cuda,"ultralytics":ultralytics.__version__,
          "device":args.device,"gpu_name":torch.cuda.get_device_name(0) if args.device=="0" else None,
          "epochs_max":args.epochs,"patience":args.patience,"batch":args.batch,"imgsz":640,
          "augmentation":{"hsv_h":0.0,"hsv_s":0.0,"hsv_v":0.0,"degrees":5.0,
                          "translate":0.08,"scale":0.2,"fliplr":0.5,"flipud":0.0,
                          "mosaic":0.0,"mixup":0.0,"cutmix":0.0,"copy_paste":0.0},
          "warning":"same old image collection; validation metrics may be optimistic. No empty scene or independent test."}
    result_file=args.output/"formal_train_result.json"
    result_file.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    freeze=subprocess.run([sys.executable,"-m","pip","freeze"],capture_output=True,text=True,check=True)
    (args.output/"pip_freeze.txt").write_text(freeze.stdout,encoding="utf-8")
    nvidia=subprocess.run(["nvidia-smi","--query-gpu=name,driver_version,memory.total,memory.free","--format=csv,noheader"],
                          capture_output=True,text=True)
    (args.output/"nvidia_smi_before.txt").write_text(nvidia.stdout+nvidia.stderr,encoding="utf-8")
    started=time.perf_counter()
    try:
        model=YOLO(str(args.pretrained.resolve()))
        model.train(data=str((args.data/"data.yaml").resolve()),epochs=args.epochs,patience=args.patience,
                    batch=args.batch,imgsz=640,device=args.device,workers=0,seed=42,deterministic=True,
                    hsv_h=0.0,hsv_s=0.0,hsv_v=0.0,degrees=5.0,translate=0.08,scale=0.2,
                    fliplr=0.5,flipud=0.0,mosaic=0.0,mixup=0.0,cutmix=0.0,copy_paste=0.0,
                    project=str(args.output.parent.resolve()),name=args.output.name,exist_ok=True,
                    plots=True,save=True,cache=False)
        if args.device=="0":torch.cuda.synchronize()
        best=args.output/"weights"/"best.pt";last=args.output/"weights"/"last.pt"
        if not best.is_file() or not last.is_file():raise FileNotFoundError("best.pt or last.pt missing after train")
        model=YOLO(str(best))
        names=tuple(model.names[i] for i in range(4))
        if names!=CLASSES:raise ValueError(f"trained class order changed: {names}")
        rows=list(csv.DictReader((args.output/"results.csv").open(encoding="utf-8")))
        info.update({"status":"completed","completed_at":now(),"elapsed_s":time.perf_counter()-started,
                     "actual_epochs_logged":len(rows),"best_pt":str(best.resolve()),"best_sha256":sha(best),
                     "last_pt":str(last.resolve()),"last_sha256":sha(last),
                     "last_training_row":rows[-1] if rows else None})
    except Exception as exc:
        info.update({"status":"failed","completed_at":now(),"elapsed_s":time.perf_counter()-started,
                     "error_type":type(exc).__name__,"error":str(exc)})
        result_file.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
        raise
    result_file.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":info["status"],"epochs":info["actual_epochs_logged"],
                      "best_pt":info["best_pt"],"output":str(args.output)},ensure_ascii=False))


if __name__=="__main__":main()
