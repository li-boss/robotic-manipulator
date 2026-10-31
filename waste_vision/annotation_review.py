"""Tkinter reviewer for pending YOLO suggestions or untouched remaining images.

Only Save reviewed label writes training-candidate labels. Suggestions stay separate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path

CLASSES = ("block_blue", "block_green", "block_red", "block_yellow")
COLORS = ("#1687ee", "#238a39", "#e33831", "#c5a000")


def prepare_manual(source: Path, trial: Path, output: Path, classes: Path) -> dict:
    actual = tuple(s.strip() for s in classes.read_text(encoding="utf-8-sig").splitlines() if s.strip())
    if actual != CLASSES:
        raise ValueError(f"unexpected class order: {actual}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    trial_names = {p.name for p in (trial / "images").glob("*.jpg")}
    images = sorted(source.glob("*.jpg"), key=lambda p: int(p.stem))
    names = [p.name for p in images if p.name not in trial_names]
    if len(images) != 206 or len(trial_names) != 20 or len(names) != 186:
        raise ValueError(f"expected 206 total / 20 trial / 186 remaining, got {len(images)}/{len(trial_names)}/{len(names)}")
    output.mkdir(parents=True)
    (output / "reviewed_labels").mkdir()
    records = [{"filename": name, "review_status": "pending"} for name in names]
    manifest = {"purpose": "186 manually reviewed candidate labels, separate from 20 trial labels",
                "source_dir": str(source.resolve()), "reviewed_labels_dir": str((output / "reviewed_labels").resolve()),
                "suggestions_dir": None, "classes": CLASSES, "images": records}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"total_source": len(images), "trial_human_labels_preserved": len(trial_names), "remaining_pending": len(names), "output": str(output)}


def load_box_file(path: Path, width: int, height: int) -> list[list[float]]:
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) != 5:
            raise ValueError(f"bad YOLO label: {path}")
        cls = int(values[0])
        cx, cy, bw, bh = map(float, values[1:])
        x1, y1, x2, y2 = (cx-bw/2)*width, (cy-bh/2)*height, (cx+bw/2)*width, (cy+bh/2)*height
        if cls not in range(4) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError(f"invalid YOLO label: {path}")
        boxes.append([cls, x1, y1, x2, y2])
    return boxes


def yolo_line(box: list[float], width: int, height: int) -> str:
    cls, x1, y1, x2, y2 = box
    if cls not in range(4) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"invalid reviewed box: {box}")
    return f"{cls} {(x1+x2)/(2*width):.6f} {(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}"


def gui(workspace: Path, start: str | None = None) -> None:
    import tkinter as tk
    from tkinter import messagebox
    from PIL import Image, ImageTk

    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    if tuple(manifest["classes"]) != CLASSES:
        raise ValueError("class order mismatch")
    source = Path(manifest["source_dir"])
    labels = Path(manifest["reviewed_labels_dir"])
    suggestions = Path(manifest["suggestions_dir"]) if manifest.get("suggestions_dir") else None
    names = [record["filename"] for record in manifest["images"]]
    if start and start not in names:
        raise ValueError(f"not in workspace: {start}")
    pending_path = workspace / "needs_recheck.json"
    recheck = set(json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else [])
    allow_verified_empty = manifest.get("allow_verified_empty") is True
    empty_path = workspace / "verified_empty.json"
    verified_empty = set(json.loads(empty_path.read_text(encoding="utf-8")) if empty_path.exists() else [])

    root = tk.Tk()
    root.title(f"积木人工审核/画框 - {workspace.name}")
    root.resizable(False, False)
    canvas = tk.Canvas(root, width=640, height=480, cursor="crosshair", bg="white")
    canvas.grid(row=0, column=0, rowspan=12, padx=8, pady=8)
    side = tk.Frame(root, padx=8)
    side.grid(row=0, column=1, sticky="n")
    heading, status = tk.StringVar(), tk.StringVar()
    tk.Label(side, textvariable=heading, width=36, anchor="w", font=("Arial", 11, "bold")).pack(anchor="w")
    tk.Label(side, textvariable=status, width=36, anchor="w", justify="left", wraplength=260).pack(anchor="w", pady=6)
    tk.Label(side, text="类别顺序固定；点选框后可改类别", anchor="w").pack(anchor="w", pady=(8, 2))
    category = tk.IntVar(value=0)
    state = {"index": 0, "boxes": [], "photo": None, "size": (640,480), "selected": None,
             "start": None, "mode": None, "draft": None, "dirty": False, "origin": "manual"}

    def name() -> str:
        return names[state["index"]]

    def label_path() -> Path:
        return labels / (Path(name()).stem + ".txt")

    def draw() -> None:
        canvas.delete("boxes")
        for idx, (cls,x1,y1,x2,y2) in enumerate(state["boxes"]):
            selected = idx == state["selected"]
            canvas.create_rectangle(x1,y1,x2,y2,outline=COLORS[cls],width=4 if selected else 2,
                                    dash=() if label_path().exists() else (6,3),tags="boxes")
            canvas.create_text(x1+2,y1+2,text=f"{idx+1}: {cls} {CLASSES[cls]}",anchor="nw",
                               fill=COLORS[cls],font=("Arial",10,"bold"),tags="boxes")
        done = sum((labels / (Path(n).stem + ".txt")).exists() for n in names)
        heading.set(f"{state['index']+1}/{len(names)}  {name()}")
        status.set(f"人工已保存 {done}/{len(names)}；当前 {len(state['boxes'])} 框\n"
                   f"当前来源：{state['origin']}；待复核：{'是' if name() in recheck else '否'}\n"
                   f"未点“人工确认并保存”前，预测框不算标签。")

    def load(index: int) -> None:
        if state["dirty"] and not messagebox.askyesno("未保存", "当前修改尚未人工确认并保存，丢弃修改吗？"):
            return
        state["index"] = index % len(names)
        img_path = source / name()
        with Image.open(img_path) as image:
            image.load()
            if image.size != (640,480):
                raise ValueError(f"expected original 640x480: {img_path}")
            state["photo"] = ImageTk.PhotoImage(image.convert("RGB"))
        canvas.delete("all")
        canvas.create_image(0,0,image=state["photo"],anchor="nw")
        path = label_path()
        suggestion_path = suggestions / (Path(name()).stem + ".txt") if suggestions else None
        if path.exists():
            state["boxes"] = load_box_file(path,640,480)
            state["origin"] = "human reviewed"
        elif suggestion_path and suggestion_path.exists():
            state["boxes"] = load_box_file(suggestion_path,640,480)
            state["origin"] = "MODEL SUGGESTION, unreviewed"
        else:
            state["boxes"] = []
            state["origin"] = "manual, unreviewed"
        state["selected"] = None
        state["dirty"] = False
        draw()

    def category_change(*_) -> None:
        idx = state["selected"]
        if idx is not None and state["boxes"][idx][0] != category.get():
            state["boxes"][idx][0] = category.get()
            state["dirty"] = True
            draw()

    def press(event) -> None:
        x,y=max(0,min(640,event.x)),max(0,min(480,event.y))
        for idx in range(len(state["boxes"])-1,-1,-1):
            cls,x1,y1,x2,y2=state["boxes"][idx]
            for corner,px,py in (("tl",x1,y1),("tr",x2,y1),("bl",x1,y2),("br",x2,y2)):
                if abs(x-px)<=9 and abs(y-py)<=9:
                    state["selected"]=idx
                    state["mode"]=corner
                    state["start"]=(x,y)
                    category.set(cls)
                    draw()
                    return
        state["mode"]="new"
        state["start"]=(x,y)
        state["draft"]=canvas.create_rectangle(x,y,x,y,outline=COLORS[category.get()],dash=(4,3),width=2)

    def drag(event) -> None:
        if state["start"] is None:
            return
        x,y=max(0,min(640,event.x)),max(0,min(480,event.y))
        if state["mode"]=="new":
            canvas.coords(state["draft"],*state["start"],x,y)
        else:
            box=state["boxes"][state["selected"]]
            old=box[:]
            corner=state["mode"]
            if "l" in corner: box[1]=min(x,box[3]-4)
            if "r" in corner: box[3]=max(x,box[1]+4)
            if "t" in corner: box[2]=min(y,box[4]-4)
            if "b" in corner: box[4]=max(y,box[2]+4)
            if box!=old: state["dirty"]=True
            draw()

    def release(event) -> None:
        if state["start"] is None: return
        x,y=max(0,min(640,event.x)),max(0,min(480,event.y))
        if state["mode"]=="new":
            x0,y0=state["start"]
            canvas.delete(state["draft"])
            if abs(x-x0)>=4 and abs(y-y0)>=4:
                x1,x2=sorted((x0,x)); y1,y2=sorted((y0,y))
                state["boxes"].append([category.get(),x1,y1,x2,y2])
                state["selected"]=len(state["boxes"])-1
                state["dirty"]=True
            else:
                state["selected"]=next((i for i in range(len(state["boxes"])-1,-1,-1)
                                        if state["boxes"][i][1]<=x<=state["boxes"][i][3]
                                        and state["boxes"][i][2]<=y<=state["boxes"][i][4]),None)
                if state["selected"] is not None:
                    category.set(state["boxes"][state["selected"]][0])
        state["start"]=state["mode"]=state["draft"]=None
        draw()

    def delete_selected() -> None:
        if state["selected"] is None: return
        state["boxes"].pop(state["selected"])
        state["selected"]=None
        state["dirty"]=True
        draw()

    def right_click(event) -> None:
        for i in range(len(state["boxes"])-1,-1,-1):
            _,x1,y1,x2,y2=state["boxes"][i]
            if x1<=event.x<=x2 and y1<=event.y<=y2:
                state["selected"]=i
                delete_selected()
                return

    def save() -> None:
        if not state["boxes"]:
            messagebox.showwarning("未确认空场景", "这批图片均待人工画框；没有框不能保存为空场景。请补框或标记待复核。")
            return
        lines=[yolo_line(box,640,480) for box in state["boxes"]]
        path=label_path()
        if path.exists():
            shutil.copy2(path,path.with_suffix(".txt.bak"))
        path.write_text("\n".join(lines)+"\n",encoding="utf-8")
        state["dirty"]=False
        state["origin"]="human reviewed"
        recheck.discard(name())
        if name() in verified_empty:
            verified_empty.discard(name())
            empty_path.write_text(json.dumps(sorted(verified_empty),ensure_ascii=False,indent=2),encoding="utf-8")
        pending_path.write_text(json.dumps(sorted(recheck),ensure_ascii=False,indent=2),encoding="utf-8")
        draw()

    def save_verified_empty() -> None:
        if not allow_verified_empty:
            return
        if state["boxes"]:
            messagebox.showwarning("仍有积木框", "请先检查并删除错误框；仅真正无积木的图片可确认空场景。")
            return
        if not messagebox.askyesno("人工确认空场景", f"已检查完整画面，确认 {name()} 没有积木吗？"):
            return
        path=label_path()
        if path.exists():
            shutil.copy2(path,path.with_suffix(".txt.bak"))
        path.write_text("",encoding="utf-8")
        verified_empty.add(name())
        empty_path.write_text(json.dumps(sorted(verified_empty),ensure_ascii=False,indent=2),encoding="utf-8")
        recheck.discard(name())
        pending_path.write_text(json.dumps(sorted(recheck),ensure_ascii=False,indent=2),encoding="utf-8")
        state["dirty"]=False
        state["origin"]="human verified empty"
        draw()

    def mark_recheck() -> None:
        recheck.add(name())
        pending_path.write_text(json.dumps(sorted(recheck),ensure_ascii=False,indent=2),encoding="utf-8")
        draw()

    for idx,cls in enumerate(CLASSES):
        tk.Radiobutton(side,text=f"{idx} {cls}",variable=category,value=idx,fg=COLORS[idx]).pack(anchor="w")
    category.trace_add("write",category_change)
    tk.Label(side,text="左键拖动：新框；点框：选中\n拖框角：调整边界；右键/删除：删框\n选中框后点类别：改类别\nS 保存，N 下一张，P 上一张\n不确定就标待复核，不要猜",justify="left").pack(anchor="w",pady=8)
    tk.Button(side,text="人工确认并保存 S",command=save,width=24).pack(pady=2)
    if allow_verified_empty:
        tk.Button(side,text="人工确认无积木并保存空标签",command=save_verified_empty,width=24).pack(pady=2)
    tk.Button(side,text="删除选中框",command=delete_selected,width=24).pack(pady=2)
    tk.Button(side,text="标记待复核",command=mark_recheck,width=24).pack(pady=2)
    tk.Button(side,text="上一张 P",command=lambda:load(state["index"]-1),width=24).pack(pady=2)
    tk.Button(side,text="下一张 N",command=lambda:load(state["index"]+1),width=24).pack(pady=2)
    canvas.bind("<ButtonPress-1>",press); canvas.bind("<B1-Motion>",drag)
    canvas.bind("<ButtonRelease-1>",release); canvas.bind("<Button-3>",right_click)
    def key(event):
        key=event.keysym.lower()
        if key in "1234" and len(key)==1: category.set(int(key)-1)
        elif key=="s": save()
        elif key=="n": load(state["index"]+1)
        elif key=="p": load(state["index"]-1)
        elif key=="delete": delete_selected()
    root.bind("<KeyPress>",key)
    load(names.index(start) if start else 0)
    root.mainloop()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("prepare-manual","gui"))
    parser.add_argument("--source",type=Path,default=Path("data/a_blocks/annotation_work"))
    parser.add_argument("--trial",type=Path,default=Path("data/a_blocks/trial20"))
    parser.add_argument("--output",type=Path,default=Path("data/a_blocks/manual186"))
    parser.add_argument("--classes",type=Path,default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--workspace",type=Path)
    parser.add_argument("--start")
    args=parser.parse_args()
    if args.command=="prepare-manual":
        print(json.dumps(prepare_manual(args.source,args.trial,args.output,args.classes),ensure_ascii=False,indent=2))
    else:
        gui(args.workspace or args.output,args.start)
