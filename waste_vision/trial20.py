"""Prepare and manually annotate a fixed 20-image trial without touching originals.

Preparation needs Pillow to make Tk-compatible PNG previews. The annotation GUI
itself uses only Python's standard-library tkinter and saves human-drawn YOLO boxes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

CLASSES = ("block_blue", "block_green", "block_red", "block_yellow")


def pixels_to_yolo(class_id: int, x1: float, y1: float, x2: float, y2: float,
                   width: int = 640, height: int = 480) -> str:
    if class_id not in range(4) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("box or class outside image")
    return f"{class_id} {(x1+x2)/(2*width):.6f} {(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}"


def read_manifest(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 20 or len({row["filename"] for row in rows}) != 20:
        raise ValueError("trial manifest must contain exactly 20 unique filenames")
    return rows


def prepare(manifest: Path, source: Path, target: Path, classes_file: Path) -> dict:
    from PIL import Image

    classes = tuple(x.strip() for x in classes_file.read_text(encoding="utf-8").splitlines() if x.strip())
    if classes != CLASSES:
        raise ValueError("confirmed classes order does not match the trial tool")
    rows = read_manifest(manifest)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing trial workspace: {target}")
    for row in rows:
        name = row["filename"]
        if Path(name).name != name or Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError(f"invalid filename: {name}")
        if not (source / name).is_file():
            raise FileNotFoundError(source / name)
    target.mkdir(parents=True)
    for subdir in ("images", "previews", "labels"):
        (target / subdir).mkdir()
    records = []
    for row in rows:
        name = row["filename"]
        original = source / name
        copy = target / "images" / name
        shutil.copy2(original, copy)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if hashlib.sha256(copy.read_bytes()).hexdigest() != digest:
            raise IOError(f"copied image differs: {name}")
        with Image.open(copy) as img:
            img.load()
            img.convert("RGB").save(target / "previews" / f"{Path(name).stem}.png")
            size = list(img.size)
        records.append({**row, "sha256": digest, "size": size, "original": str(original)})
    (target / "manifest.json").write_text(json.dumps({"classes": CLASSES, "images": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"images": len(records), "target": str(target), "labels": 0}


def gui(target: Path, classes_file: Path, start_filename: str | None = None) -> None:
    import tkinter as tk
    from tkinter import messagebox

    classes = tuple(x.strip() for x in classes_file.read_text(encoding="utf-8").splitlines() if x.strip())
    if classes != CLASSES:
        raise ValueError("confirmed classes order changed")
    records = json.loads((target / "manifest.json").read_text(encoding="utf-8"))["images"]
    filenames = [record["filename"] for record in records]
    if start_filename is not None and start_filename not in filenames:
        raise ValueError(f"filename not in this trial: {start_filename}")
    root = tk.Tk()
    root.title("积木试标 20 张｜人工画框｜YOLO")
    root.resizable(False, False)
    canvas = tk.Canvas(root, width=640, height=480, bg="white", cursor="crosshair")
    canvas.grid(row=0, column=0, rowspan=10, padx=8, pady=8)
    panel = tk.Frame(root, padx=8)
    panel.grid(row=0, column=1, sticky="n")
    title = tk.StringVar()
    tk.Label(panel, textvariable=title, width=34, anchor="w", font=("Arial", 11, "bold")).pack(anchor="w")
    tk.Label(panel, text="选类别（键盘 1–4），再在图片上拖框：", anchor="w").pack(anchor="w", pady=(12, 2))
    selected = tk.IntVar(value=0)
    colors = ("#2196f3", "#26882f", "#e43b35", "#bd9a00")
    for index, name in enumerate(CLASSES):
        tk.Radiobutton(panel, text=f"{index}  {name}", variable=selected, value=index, fg=colors[index]).pack(anchor="w")
    status = tk.StringVar()
    tk.Label(panel, textvariable=status, anchor="w", justify="left", wraplength=260).pack(anchor="w", pady=12)
    tk.Label(panel, text="左键拖动：新增框\n右键点框：删除该框\nS：保存  N：下一张  P：上一张\n不确定：点击「待复核」，不要猜", justify="left", anchor="w").pack(anchor="w", pady=8)

    state = {"index": 0, "boxes": [], "photo": None, "start": None, "draft": None, "dirty": False}
    review_path = target / "pending_review.json"
    reviews = set(json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else [])

    def label_path() -> Path:
        return target / "labels" / f'{Path(records[state["index"]]["filename"]).stem}.txt'

    def draw() -> None:
        canvas.delete("box")
        for class_id, x1, y1, x2, y2 in state["boxes"]:
            canvas.create_rectangle(x1, y1, x2, y2, outline=colors[class_id], width=2, tags="box")
            canvas.create_text(x1 + 2, y1 + 2, text=f"{class_id} {CLASSES[class_id]}", anchor="nw", fill=colors[class_id], font=("Arial", 10, "bold"), tags="box")
        name = records[state["index"]]["filename"]
        title.set(f'{state["index"] + 1}/20  {name}')
        status.set(f'框数：{len(state["boxes"])}  已保存：{"是" if label_path().exists() else "否"}\n待复核：{"是" if name in reviews else "否"}')

    def load(index: int) -> None:
        if state["dirty"] and not messagebox.askyesno("尚未保存", "当前框未保存。放弃这些更改并切换图片吗？"):
            return
        state["index"] = index % len(records)
        name = records[state["index"]]["filename"]
        state["photo"] = tk.PhotoImage(file=str(target / "previews" / f"{Path(name).stem}.png"))
        canvas.delete("all")
        canvas.create_image(0, 0, image=state["photo"], anchor="nw")
        width, height = records[state["index"]]["size"]
        if (width, height) != (640, 480):
            raise ValueError(f"trial GUI requires 640x480 previews: {name}")
        state["boxes"] = []
        if label_path().exists():
            for line in label_path().read_text(encoding="utf-8").splitlines():
                values = line.split()
                if len(values) != 5:
                    raise ValueError(f"bad existing label: {label_path()}")
                class_id = int(values[0])
                cx, cy, bw, bh = map(float, values[1:])
                if class_id not in range(4) or bw <= 0 or bh <= 0 or cx - bw / 2 < 0 or cx + bw / 2 > 1 or cy - bh / 2 < 0 or cy + bh / 2 > 1:
                    raise ValueError(f"invalid existing label: {label_path()}")
                state["boxes"].append((class_id, (cx - bw / 2) * width, (cy - bh / 2) * height, (cx + bw / 2) * width, (cy + bh / 2) * height))
        state["dirty"] = False
        draw()

    def start(event) -> None:
        state["start"] = (max(0, min(640, event.x)), max(0, min(480, event.y)))
        state["draft"] = canvas.create_rectangle(*state["start"], *state["start"], outline=colors[selected.get()], dash=(4, 3), width=2)

    def drag(event) -> None:
        if state["start"] is not None:
            canvas.coords(state["draft"], *state["start"], max(0, min(640, event.x)), max(0, min(480, event.y)))

    def finish(event) -> None:
        if state["start"] is None:
            return
        x0, y0 = state["start"]
        x, y = max(0, min(640, event.x)), max(0, min(480, event.y))
        canvas.delete(state["draft"])
        state["start"] = state["draft"] = None
        x1, x2 = sorted((x0, x))
        y1, y2 = sorted((y0, y))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return
        state["boxes"].append((selected.get(), x1, y1, x2, y2))
        state["dirty"] = True
        draw()

    def delete(event) -> None:
        for i in range(len(state["boxes"]) - 1, -1, -1):
            _, x1, y1, x2, y2 = state["boxes"][i]
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                state["boxes"].pop(i)
                state["dirty"] = True
                draw()
                break

    def save() -> None:
        if not state["boxes"]:
            messagebox.showwarning("没有框", "这 20 张试标图均有积木；请画框，或标记待复核。不能把未标注图片保存为空场景。")
            return
        lines = []
        for class_id, x1, y1, x2, y2 in state["boxes"]:
            lines.append(pixels_to_yolo(class_id, x1, y1, x2, y2))
        path = label_path()
        if path.exists() and state["dirty"]:
            shutil.copy2(path, path.with_suffix(".txt.bak"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state["dirty"] = False
        draw()

    def review() -> None:
        name = records[state["index"]]["filename"]
        if name in reviews:
            reviews.remove(name)
            message = f"已解除 {name} 的待复核标记；请确认框和类别后保存。"
        else:
            reviews.add(name)
            message = f"已记录 {name}；请先跳过，不猜测类别。"
        review_path.write_text(json.dumps(sorted(reviews), ensure_ascii=False, indent=2), encoding="utf-8")
        draw()
        messagebox.showinfo("待复核", message)

    tk.Button(panel, text="保存当前框  S", command=save, width=22).pack(pady=3)
    tk.Button(panel, text="上一张  P", command=lambda: load(state["index"] - 1), width=22).pack(pady=3)
    tk.Button(panel, text="下一张  N", command=lambda: load(state["index"] + 1), width=22).pack(pady=3)
    tk.Button(panel, text="切换待复核标记", command=review, width=22).pack(pady=3)
    canvas.bind("<ButtonPress-1>", start)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", finish)
    canvas.bind("<Button-3>", delete)
    root.bind("<KeyPress>", lambda e: selected.set(int(e.char) - 1) if e.char in "1234" else save() if e.char.lower() == "s" else load(state["index"] + 1) if e.char.lower() == "n" else load(state["index"] - 1) if e.char.lower() == "p" else None)
    load(filenames.index(start_filename) if start_filename else 0)
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "gui"))
    parser.add_argument("--manifest", type=Path, default=Path("docs/a/数据审查/试标20张清单.csv"))
    parser.add_argument("--source", type=Path, default=Path("data/a_blocks/annotation_work"))
    parser.add_argument("--target", type=Path, default=Path("data/a_blocks/trial20"))
    parser.add_argument("--classes", type=Path, default=Path("configs/a_confirmed_classes.txt"))
    parser.add_argument("--start", help="filename to show first in GUI, e.g. 414.jpg")
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        print(json.dumps(prepare(arguments.manifest, arguments.source, arguments.target, arguments.classes), ensure_ascii=False))
    else:
        gui(arguments.target, arguments.classes, arguments.start)
