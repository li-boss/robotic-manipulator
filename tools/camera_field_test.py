"""On-site A camera check: real frames, D JPEG access, detection, and scene evidence.

Run only after the real camera is connected. No robot commands and no synthetic detections.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SCENES = (
    ("blue", "只放蓝色顶面积木"),
    ("green", "只放绿色顶面积木"),
    ("red", "只放红色顶面积木"),
    ("yellow", "只放黄色顶面积木"),
    ("multiple", "同时放置多块积木"),
    ("moved_rotated", "移动位置并旋转积木"),
    ("empty", "移走全部积木"),
    ("cross_background", "仅保留十字标记和背景"),
    ("lighting", "调整实际使用范围内的光照"),
)


def call(base: str, path: str, payload: dict | None = None) -> tuple[int, bytes, float]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(), round((time.perf_counter() - start) * 1000, 3)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), round((time.perf_counter() - start) * 1000, 3)


def json_call(base: str, path: str, payload: dict | None = None) -> tuple[int, dict, float]:
    code, raw, elapsed = call(base, path, payload)
    return code, json.loads(raw.decode("utf-8")), elapsed


def save_overlay(raw: bytes, targets: list[dict], path: Path | None,
                 show_box_center: bool = False, view: str = "overlay"):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("D returned invalid JPEG")
    labels = []
    for index, target in enumerate(targets, 1):
        x1, y1, x2, y2 = [round(v) for v in target["bbox"]]
        if view != "segmentation":
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 255), 2)
        face = target.get("top_face") or {}
        contour = face.get("visible_contour")
        if contour and len(contour) >= 3:
            polygon = np.asarray(contour, dtype=np.int32)
            layer = image.copy()
            cv2.fillPoly(layer, [polygon], (0, 180, 255))
            image = cv2.addWeighted(layer, 0.16, image, 0.84, 0)
            cv2.polylines(image, [polygon], True, (0, 160, 255), 1)
        if view == "segmentation":
            continue
        quad = face.get("quad")
        if quad:
            cv2.polylines(image, [np.rint(quad).astype(np.int32)], True, (0, 220, 0), 1)
        point = face.get("candidate_point") if face.get("status") == "candidate" else None
        if point:
            center = (round(point["x"]), round(point["y"]))
            cv2.drawMarker(image, center, (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
            cv2.circle(image, center, 4, (0, 0, 0), -1)
            status = f"顶面({center[0]},{center[1]}) 对角线交点"
        else:
            centroid = face.get("visible_centroid")
            if centroid:
                cv2.circle(image, (round(centroid["x"]), round(centroid["y"])),
                           4, (0, 150, 255), -1)
            status = "顶面中心待确认"
        if show_box_center:
            box_center = (round(target["center_x"]), round(target["center_y"]))
            cv2.drawMarker(image, box_center, (255, 255, 0), cv2.MARKER_TILTED_CROSS, 14, 1)
        if show_box_center:
            status += f" | 框中心{box_center}"
        labels.append(f"{index}. {target['raw_label']} {target['confidence']:.2f} | {status}")
        cv2.putText(image, str(index), (max(0, x1), max(14, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
    legend_height = max(28, 8 + 26 * len(labels))
    canvas = Image.new("RGB", (image.shape[1], image.shape[0] + legend_height), "white")
    canvas.paste(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)), (0, 0))
    draw = ImageDraw.Draw(canvas)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    font = ImageFont.truetype(str(font_path), 14) if font_path.is_file() else ImageFont.load_default()
    for index, label in enumerate(labels):
        draw.text((8, image.shape[0] + 4 + 26 * index), label, font=font, fill=(0, 0, 0))
    if not labels:
        legend = "分割区域（橙色）；未画可靠顶面中心" if view == "segmentation" and targets else "无检测目标"
        draw.text((8, image.shape[0] + 4), legend, font=font, fill=(0, 0, 0))
    image = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
    if path is not None and not cv2.imwrite(str(path), image):
        raise OSError(f"could not save {path}")
    return image


def interactive_preview(base: str, root: Path, show_box_center: bool = False) -> None:
    """Small live OpenCV viewer. Every saved record uses the displayed frame ID."""
    import cv2
    import numpy as np

    root.mkdir(parents=True, exist_ok=False)
    view = "overlay"
    print("预览键：1 原图，2 分割，3 定位叠加，S 保存，C 正确，P 偏移，R 本应复核，M 漏检，F 误检，Q 退出")
    try:
        while True:
            try:
                ready_code, ready, _ = json_call(base, "/api/v1/ready")
                if ready_code != 200 or ready.get("simulated") is not False:
                    print("服务尚未就绪：", ready.get("camera_error", ready.get("status")))
                    time.sleep(.3)
                    if cv2.waitKey(1) & 0xff == ord("q"):
                        break
                    continue
                if ready.get("top_face_config_sha256") is None:
                    raise RuntimeError("请用 --top-face 启动真实服务后再打开顶面预览")
                frame_id = ready["latest_frame"]["frame_id"]
                image_code, raw, _ = call(base, "/api/v1/frames/" + frame_id)
                detect_code, result, _ = json_call(
                    base, "/api/v1/detect",
                    {"request_id": "preview-" + uuid4().hex, "frame_id": frame_id})
                if image_code != 200 or detect_code != 200 or result["frame_id"] != frame_id:
                    print("本帧不可用或已过期：", detect_code, result.get("code"))
                    continue
                if view == "raw":
                    shown = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                else:
                    shown = save_overlay(raw, result["targets"], None, show_box_center,
                                         "segmentation" if view == "segmentation" else "overlay")
                cv2.imshow("A top face live preview - Q to quit", shown)
                key = cv2.waitKey(1) & 0xff
                if key == ord("q"):
                    break
                if key in (ord("1"), ord("2"), ord("3")):
                    view = {ord("1"): "raw", ord("2"): "segmentation", ord("3"): "overlay"}[key]
                if key in (ord("s"), ord("c"), ord("p"), ord("r"), ord("m"), ord("f")):
                    review = {ord("c"): "center_correct", ord("p"): "center_offset",
                              ord("r"): "should_review", ord("m"): "missed_detection",
                              ord("f"): "false_detection"}.get(key)
                    target_index = None
                    reason = None
                    if review:
                        print("当前帧目标：", [(i + 1, t["raw_label"], t["target_id"])
                                                for i, t in enumerate(result["targets"])])
                        chosen = input("目标序号（整帧问题留空）：").strip()
                        if chosen:
                            if not chosen.isdigit() or not 1 <= int(chosen) <= len(result["targets"]):
                                print("序号无效，未保存本次标记")
                                continue
                            target_index = int(chosen) - 1
                        reason = input("简短原因（可留空）：").strip()
                    folder = root / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + frame_id)
                    folder.mkdir(parents=True, exist_ok=False)
                    (folder / "raw.jpg").write_bytes(raw)
                    save_overlay(raw, result["targets"], folder / "segmentation.jpg",
                                 view="segmentation")
                    save_overlay(raw, result["targets"], folder / "overlay.jpg", show_box_center)
                    record = {"source_type": "interactive_live_http_preview",
                              "frame_id": frame_id, "frame_timestamp_semantics":
                                  ready.get("frame_timestamp_semantics"),
                              "raw_sha256": hashlib.sha256(raw).hexdigest(),
                              "weights_sha256": ready.get("model_sha256"),
                              "top_face_config_sha256": ready.get("top_face_config_sha256"),
                              "top_face_config_version": ready.get("top_face_config_version"),
                              "algorithm_version": ready.get("top_face_algorithm_version"),
                              "model_parameters": ready.get("model_parameters"),
                              "response": result,
                              "human_quick_mark": ({"decision": review, "target_index": target_index,
                                                    "target_id": (result["targets"][target_index]["target_id"]
                                                                  if target_index is not None else None),
                                                    "reason": reason,
                                                    "note": "target_id is unique only within this frame; "
                                                            "quick marks are not complete ground truth"}
                                                   if review else None)}
                    (folder / "response.json").write_text(
                        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                    print("已保存同帧证据：", folder)
            except (OSError, ValueError, KeyError) as exc:
                print("预览取帧失败：", exc)
                time.sleep(.3)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()


def sample(base: str, folder: Path, name: str,
           show_box_center: bool = False) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    ready_code, ready, ready_ms = json_call(base, "/api/v1/ready")
    if ready_code != 200:
        record = {"scene": name, "ready_http": ready_code, "ready": ready}
        (folder / "response.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
    if ready.get("simulated") is not False or ready.get("model_ready") is not True:
        raise RuntimeError("field test requires the ready real model backend, not mock/none")
    meta = ready["latest_frame"]
    frame_id = meta["frame_id"]
    frame_path = "/api/v1/frames/" + frame_id
    # A normal consumer's JPEG and D's simultaneous request must name the same frame.
    first_code, raw, first_ms = call(base, frame_path)
    request_id = "field-" + uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        detection = pool.submit(json_call, base, "/api/v1/detect",
                                {"request_id": request_id, "frame_id": frame_id})
        d_frame = pool.submit(call, base, frame_path)
        detect_code, result, detect_ms = detection.result()
        d_code, d_raw, d_ms = d_frame.result()
    if first_code == 200:
        (folder / "raw.jpg").write_bytes(raw)
    record = {"scene": name, "frame_meta": meta, "request_id": request_id,
              "provenance": {"source_type": ("live_camera_http" if ready.get("capture_mode") == "camera"
                                             else "saved_image_replay" if ready.get("capture_mode") == "image"
                                             else "other_or_unknown"),
                             "raw_sha256": hashlib.sha256(raw).hexdigest() if first_code == 200 else None,
                             "weights_sha256": ready.get("model_sha256"),
                             "top_face_config_sha256": ready.get("top_face_config_sha256"),
                             "top_face_config_version": ready.get("top_face_config_version"),
                             "algorithm_version": ready.get("top_face_algorithm_version"),
                             "model_parameters": ready.get("model_parameters"),
                             "captured_at_semantics": ready.get("frame_timestamp_semantics"),
                             "source_exposure_timestamp_available":
                                 ready.get("upstream_exposure_timestamp_available", False)},
              "ready_ms": ready_ms, "first_frame_http": first_code,
              "first_frame_ms": first_ms, "detect_http": detect_code,
              "detect_wall_ms": detect_ms, "d_frame_http": d_code,
              "d_frame_ms": d_ms, "d_frame_same_bytes": first_code == d_code == 200 and raw == d_raw,
              "response": result}
    if detect_code == 200 and first_code == 200:
        if result["frame_id"] != frame_id:
            raise ValueError("detector used a different frame")
        if ready.get("top_face_config_sha256"):
            save_overlay(raw, result["targets"], folder / "segmentation.jpg",
                         view="segmentation")
        save_overlay(raw, result["targets"], folder / "overlay.jpg", show_box_center)
    (folder / "response.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Real camera field capture; no mock results")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--out", default="data/a_blocks/camera_field_test")
    parser.add_argument("--probe-seconds", type=float, default=6.0)
    parser.add_argument("--scenes", action="store_true", help="prompt for physical block scenes")
    parser.add_argument("--interactive-preview", action="store_true",
                        help="show raw/segmentation/overlay and save marked same-frame evidence")
    parser.add_argument("--show-box-center", action="store_true",
                        help="debug: show original bbox center as a cyan X")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if args.interactive_preview:
        root = Path(args.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
        interactive_preview(base, root, args.show_box_center)
        return
    root = Path(args.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=False)
    seen = []
    until = time.monotonic() + max(0, args.probe_seconds)
    while time.monotonic() < until:
        try:
            code, body, _ = json_call(base, "/api/v1/ready")
            if code == 200:
                meta = body["latest_frame"]
                seen.append({"frame_id": meta["frame_id"], "captured_at": meta["captured_at"]})
            else:
                seen.append({"http": code, "status": body.get("status"),
                             "camera_error": body.get("camera_error")})
        except (OSError, ValueError) as exc:
            seen.append({"error": str(exc)})
        time.sleep(0.2)
    unique = {row["frame_id"] for row in seen if "frame_id" in row}
    summary = {"probe_seconds": args.probe_seconds, "samples": len(seen),
               "unique_frame_ids": len(unique), "observations": seen}
    (root / "continuous_frames.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"连续采帧：{len(unique)} 个不同 frame_id；记录：{root}")
    if not unique:
        print("当前无可用摄像头帧；未运行真实场景检测。检查设备、权限、索引和服务就绪状态。")
        return
    if not args.scenes:
        record = sample(base, root / "single_probe", "single_probe", args.show_box_center)
        print("单帧真实接口：", record.get("detect_http"), "D同帧：", record.get("d_frame_same_bytes"))
        return
    for name, instruction in SCENES:
        input(f"请{instruction}，摆好后按 Enter 采集 [{name}]：")
        record = sample(base, root / name, name, args.show_box_center)
        print(name, "检测 HTTP", record.get("detect_http"), "D同帧", record.get("d_frame_same_bytes"))
    input("如需实测断流/恢复，请先断开摄像头并按 Enter：")
    for _ in range(50):
        code, _, _ = json_call(base, "/api/v1/ready")
        if code == 503:
            break
        time.sleep(0.2)
    sample(base, root / "disconnected", "disconnected", args.show_box_center)
    input("请重新连接摄像头并按 Enter：")
    for _ in range(50):
        code, _, _ = json_call(base, "/api/v1/ready")
        if code == 200:
            break
        time.sleep(0.2)
    sample(base, root / "recovered", "recovered", args.show_box_center)
    print("请逐张查看 raw.jpg、overlay.jpg 和 response.json；程序不判断人工真值。")


if __name__ == "__main__":
    main()
