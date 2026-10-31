"""Manual four-corner review of saved raw-camera top faces.

Click four visible top corners in clockwise order, then Save and next.
Only the original image and YOLO box are shown; predictions are hidden to
avoid copying the locator's own answer into the reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

VERSION = "manual-raw-corners-v2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="top_face_validate output folder")
    parser.add_argument("--output", required=True, help="separate user reference JSON")
    parser.add_argument("--case", action="append", help="only this case ID; repeatable")
    parser.add_argument("--frame-audit", action="store_true",
                        help="audit all real blocks in each raw frame, including model misses")
    args = parser.parse_args()
    if args.frame_audit:
        from tools.top_face_frame_audit import run_frame_audit
        run_frame_audit(Path(args.dataset), Path(args.output), args.case)
        return
    dataset, output = Path(args.dataset), Path(args.output)
    references = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    tasks = []
    for result_path in sorted(dataset.glob("*/result.json")):
        record = json.loads(result_path.read_text(encoding="utf-8"))
        if "case_id" not in record:
            continue
        case_id = record["case_id"]
        if args.case and case_id not in args.case:
            continue
        for index, target in enumerate(record["targets"]):
            tasks.append((case_id, index, target, result_path.parent / "raw.jpg",
                          record["frame_id"]))
    if not tasks:
        raise ValueError("no targets selected")
    app = tk.Tk()
    app.title("顶面四角人工复核：原图坐标")
    state = {"index": 0, "points": [], "photo": None}
    canvas = tk.Canvas(app, width=640, height=480, bg="black")
    canvas.pack()
    label = tk.Label(app, text="")
    label.pack()

    def show():
        if state["index"] >= len(tasks):
            label.configure(text=f"完成。已保存至 {output}")
            return
        case_id, index, target, image_path, _ = tasks[state["index"]]
        image = Image.open(image_path).convert("RGB")
        if image.size != (640, 480):
            raise ValueError(f"expected original 640x480 pixels: {image_path}")
        draw = ImageDraw.Draw(image)
        draw.rectangle(tuple(round(v) for v in target["bbox"]), outline="yellow", width=2)
        state["photo"] = ImageTk.PhotoImage(image)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=state["photo"])
        old = references.get(case_id, {}).get(str(index), {})
        state["points"] = [list(p) for p in old.get("corners") or []]
        redraw_points()
        label.configure(text=f"{state['index'] + 1}/{len(tasks)}  {case_id}  "
                             f"目标 {index}: {target['raw_label']} | 已存: "
                             f"{old.get('review_status', '未复核')} | 左键四角，右键撤销")

    def redraw_points():
        canvas.delete("point")
        for number, (x, y) in enumerate(state["points"], 1):
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="magenta",
                               outline="white", tags="point")
            canvas.create_text(x + 8, y - 8, text=str(number), fill="black", tags="point")

    def click(event):
        if state["index"] >= len(tasks) or len(state["points"]) >= 4:
            return
        if not (0 <= event.x < 640 and 0 <= event.y < 480):
            return
        state["points"].append([int(event.x), int(event.y)])
        redraw_points()

    def undo(_event=None):
        if state["points"]:
            state["points"].pop()
            redraw_points()

    def clear():
        state["points"] = []
        redraw_points()

    def record(status):
        if state["index"] >= len(tasks):
            return
        if status == "confirmed" and len(state["points"]) != 4:
            label.configure(text="完整顶面须点击四角；否则选‘不完整’或‘无法判断’。")
            return
        case_id, index, _, image_path, frame_id = tasks[state["index"]]
        references.setdefault(case_id, {})[str(index)] = {
            "review_status": status, "reference_version": VERSION,
            "source_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "frame_id": frame_id, "image_width": 640, "image_height": 480,
            "corners": state["points"] if status == "confirmed" else None,
            "note": "user GUI review on raw camera image"}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8")
        state["index"] += 1
        show()

    def skip(_event=None):
        state["index"] += 1
        show()

    def previous():
        if state["index"] > 0:
            state["index"] -= 1
            show()

    canvas.bind("<Button-1>", click)
    canvas.bind("<Button-3>", undo)
    buttons = tk.Frame(app)
    buttons.pack()
    for name, action in (("保存完整四角", lambda: record("confirmed")),
                         ("顶面不完整", lambda: record("incomplete")),
                         ("无法判断", lambda: record("uncertain")),
                         ("清空重画", clear), ("上一目标", previous), ("跳过不保存", skip)):
        tk.Button(buttons, text=name, command=action).pack(side="left")
    show()
    app.mainloop()


if __name__ == "__main__":
    main()
