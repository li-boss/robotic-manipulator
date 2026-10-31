"""Full-frame human audit GUI: every block, including missed detections.

Raw image is shown first without algorithm overlays. The user supplies an
independent whole-block box and color for every visible true object.
"""
from __future__ import annotations

import hashlib
import json
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

CLASSES = ("block_blue", "block_green", "block_red", "block_yellow")
SCHEMA = "full-frame-human-audit-v1"


def run_frame_audit(dataset: Path, output: Path, selected_cases=None):
    records = []
    for path in sorted(dataset.glob("*/result.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if selected_cases and record["case_id"] not in selected_cases:
            continue
        records.append((record, path.parent / "raw.jpg"))
    if not records:
        raise ValueError("no saved raw frames in dataset")
    audit = (json.loads(output.read_text(encoding="utf-8")) if output.exists()
             else {"schema_version": SCHEMA, "frames": {}})
    if audit.get("schema_version") != SCHEMA:
        raise ValueError("output is not a full-frame audit file; choose a new filename")
    app = tk.Tk()
    app.title("逐帧人工审查：包括 YOLO 漏检目标")
    canvas = tk.Canvas(app, width=640, height=480, bg="black")
    canvas.pack()
    status = tk.Label(app, text="", wraplength=900)
    status.pack()
    controls = tk.Frame(app)
    controls.pack()
    color = tk.StringVar(value=CLASSES[0])
    visibility = tk.StringVar(value="complete")
    tk.OptionMenu(controls, color, *CLASSES).pack(side="left")
    tk.OptionMenu(controls, visibility, "complete", "incomplete", "uncertain").pack(side="left")
    note = tk.Entry(controls, width=30)
    note.pack(side="left")
    state = {"case_index": 0, "object_index": None, "box": [], "corners": [],
             "mode": "bbox", "show_model": False, "photo": None}
    object_list = tk.Listbox(app, height=5, width=90)
    object_list.pack()

    def current():
        record, image_path = records[state["case_index"]]
        case_id = record["case_id"]
        raw = image_path.read_bytes()
        entry = audit["frames"].setdefault(case_id, {
            "review_status": "in_progress", "source_sha256": hashlib.sha256(raw).hexdigest(),
            "frame_id": record["frame_id"], "image_width": record["image_width"],
            "image_height": record["image_height"], "objects": []})
        if (entry["source_sha256"] != hashlib.sha256(raw).hexdigest() or
                entry["frame_id"] != record["frame_id"]):
            raise ValueError(f"saved audit source changed: {case_id}")
        return record, image_path, entry

    def persist():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh():
        record, image_path, entry = current()
        image = Image.open(image_path).convert("RGB")
        if image.size != (640, 480):
            raise ValueError(f"expected raw 640x480 image: {image_path}")
        if state["show_model"]:
            draw = ImageDraw.Draw(image)
            for target in record["targets"]:
                draw.rectangle(tuple(round(v) for v in target["bbox"]), outline="cyan", width=2)
        state["photo"] = ImageTk.PhotoImage(image)
        canvas.delete("all")
        canvas.create_image(0, 0, image=state["photo"], anchor="nw")
        object_list.delete(0, "end")
        for index, obj in enumerate(entry["objects"]):
            object_list.insert("end", f"{index + 1}. {obj['raw_label']}  顶面:{obj['top_visibility']}  "
                                  f"框:{obj['bbox']}  {obj.get('note', '')}")
            x1, y1, x2, y2 = obj["bbox"]
            canvas.create_rectangle(x1, y1, x2, y2, outline="lime", width=2)
            canvas.create_text(x1 + 5, y1 + 5, text=str(index + 1), fill="black", anchor="nw")
        if len(state["box"]) == 2:
            (x1, y1), (x2, y2) = state["box"]
            canvas.create_rectangle(x1, y1, x2, y2, outline="orange", width=2)
        for number, (x, y) in enumerate(state["corners"], 1):
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="magenta")
            canvas.create_text(x + 5, y - 5, text=str(number), fill="black")
        status.configure(text=f"{state['case_index'] + 1}/{len(records)} {record['case_id']} | "
                         f"审查:{entry['review_status']} | {state['mode']} 左键点位、右键撤销。"
                         "绿色=人工框；青色=可选模型框。target_id 仅本帧有效。")

    def click(event):
        if not (0 <= event.x < 640 and 0 <= event.y < 480):
            return
        key, limit = ("box", 2) if state["mode"] == "bbox" else ("corners", 4)
        if len(state[key]) < limit:
            state[key].append([int(event.x), int(event.y)])
            refresh()

    def undo(_event=None):
        key = "box" if state["mode"] == "bbox" else "corners"
        if state[key]:
            state[key].pop()
            refresh()

    def new_object():
        state.update(object_index=None, box=[], corners=[], mode="bbox")
        note.delete(0, "end")
        refresh()

    def select_object(_event=None):
        selected = object_list.curselection()
        if not selected:
            return
        index = selected[0]
        obj = current()[2]["objects"][index]
        state.update(object_index=index, box=[[obj["bbox"][0], obj["bbox"][1]],
                                               [obj["bbox"][2], obj["bbox"][3]]],
                     corners=[list(p) for p in obj.get("corners") or []], mode="bbox")
        color.set(obj["raw_label"])
        visibility.set(obj["top_visibility"])
        note.delete(0, "end")
        note.insert(0, obj.get("note", ""))
        refresh()

    def save_object():
        if len(state["box"]) != 2:
            status.configure(text="先点击整块积木可见范围的框左上角和右下角。")
            return
        (x1, y1), (x2, y2) = state["box"]
        if x2 <= x1 or y2 <= y1:
            status.configure(text="整块框必须由左上角、右下角组成。")
            return
        corners = state["corners"] if visibility.get() == "complete" else None
        if visibility.get() == "complete":
            from tools.top_face_validate import checked_reference_center
            try:
                checked_reference_center(corners, 640, 480)
            except ValueError as exc:
                status.configure(text=f"完整顶面需沿轮廓顺序点击四角: {exc}")
                return
        entry = current()[2]
        obj = {"raw_label": color.get(), "bbox": [x1, y1, x2, y2],
               "top_visibility": visibility.get(), "corners": corners,
               "note": note.get().strip()}
        if state["object_index"] is None:
            entry["objects"].append(obj)
        else:
            entry["objects"][state["object_index"]] = obj
        entry["review_status"] = "in_progress"
        persist()
        new_object()

    def delete_object():
        if state["object_index"] is not None:
            current()[2]["objects"].pop(state["object_index"])
            current()[2]["review_status"] = "in_progress"
            persist()
            new_object()

    def complete_frame():
        entry = current()[2]
        entry["review_status"] = "complete"
        persist()
        status.configure(text="本帧已人工确认全部真实积木；零目标时表示人工确认空场景。")

    def move(delta):
        target = state["case_index"] + delta
        if 0 <= target < len(records):
            state.update(case_index=target, object_index=None, box=[], corners=[], mode="bbox")
            note.delete(0, "end")
            refresh()

    canvas.bind("<Button-1>", click)
    canvas.bind("<Button-3>", undo)
    object_list.bind("<<ListboxSelect>>", select_object)
    buttons = tk.Frame(app)
    buttons.pack()
    for text, action in (("新增积木", new_object),
                         ("重画整块框", lambda: (state.update(box=[], mode="bbox"), refresh())),
                         ("画顶面四角", lambda: state.update(corners=[], mode="corners")),
                         ("保存积木", save_object), ("删除选中", delete_object),
                         ("本帧全部审完", complete_frame),
                         ("显示/隐藏模型框", lambda: (state.update(show_model=not state["show_model"]), refresh())),
                         ("上一帧", lambda: move(-1)), ("下一帧", lambda: move(1))):
        tk.Button(buttons, text=text, command=action).pack(side="left")
    refresh()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass
