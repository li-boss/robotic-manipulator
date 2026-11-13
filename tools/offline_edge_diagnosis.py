"""Read local evidence and audit saved-image pixels. No server or hardware access.

Outputs go only to a new --out directory. Original workspaces remain read-only.
The 8099 service is invoked as Python functions with a replaced board transport;
its protocol fixture contains no invented joint readings or calibration.
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OLD = Path('G:/robotic-manipulator')
SKIP = {'.git', '__pycache__', 'node_modules', 'site-packages'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def identifiers(value):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('frame_id', 'request_id', 'session_id', 'captured_at', 'board_captured_at',
                       'received_at_pc', 'receive_started_at_pc', 'staged_at', 'raw_sha256', 'jpeg_sha256'):
                found.append([key, item])
            elif key == 'joints' and isinstance(item, list):
                found.append(['joint_readings', item])
            elif isinstance(item, (dict, list)):
                found.extend(identifiers(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(identifiers(item))
    return found


def inventory(roots, out):
    records, snapshots, planning, progress, freezes, json_errors = [], [], [], [], [], []
    counts = {}
    for root in roots:
        scanned = 0
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.venv') and
                       not (Path(folder) / d).is_relative_to(out)]
            if Path(folder).name.startswith('freeze_'):
                freezes.append(str(Path(folder)))
            for name in files:
                path = Path(folder) / name
                if name.endswith('.request.json'):
                    planning.append(str(path))
                if name.endswith('.progress.jsonl'):
                    progress.append(str(path))
                if path.suffix != '.json':
                    continue
                scanned += 1
                try:
                    data = load(path)
                except (ValueError, OSError) as exc:
                    json_errors.append(dict(path=str(path), error=str(exc)))
                    continue
                if isinstance(data, dict) and {'setup', 'arms', 'target'} <= data.keys():
                    snapshots.append(str(path))
                facts = identifiers(data)
                if facts or name in ('arm_config.json', 'manipulation_setup.json'):
                    records.append(dict(path=str(path), sha256=digest(path), facts=facts,
                                        top_keys=sorted(data) if isinstance(data, dict) else None))
        counts[str(root)] = scanned
    by_frame = {}
    for record in records:
        for key, value in record['facts']:
            if key == 'frame_id' and isinstance(value, str):
                by_frame.setdefault(value, set()).add(record['path'])
    result = dict(roots=[str(r) for r in roots], json_counts=counts,
                  planning_requests=planning, planning_progress=progress, planning_snapshots=snapshots,
                  freeze_directories=freezes, metadata_records=records, json_errors=json_errors,
                  frame_matches={k: sorted(v) for k, v in by_frame.items()})
    write(out / 'evidence_inventory.json', result)
    return result


def forbid_network(*args, **kwargs):
    raise RuntimeError('Offline diagnosis: network access is disabled')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inv = inventory([ROOT, OLD], out)
    if inv['planning_snapshots'] or inv['planning_requests']:
        raise RuntimeError('Planning requests found: review inventory and replay matching original before selecting a surrogate')

    # No network, external service, hardware constructor or model download.
    socket.socket.connect = socket.socket.connect_ex = forbid_network
    socket.create_connection = forbid_network
    forbidden = types.ModuleType('Arm_Lib')
    forbidden.Arm_Device = forbid_network
    sys.modules['Arm_Lib'] = forbidden
    os.environ['YOLO_OFFLINE'] = 'true'
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    os.environ['YOLO_CONFIG_DIR'] = str(out / 'ultralytics_config')
    sys.path.insert(0, str(ROOT / 'pc'))
    sys.path.insert(0, str(ROOT / 'board'))
    import cv2
    import numpy as np
    import ultralytics
    from block_vision.yolo_detector import YoloBlockDetector
    from block_vision.top_face import diagonal_intersection
    from modes import detection_to_target
    import vision_service

    source = OLD / 'data/a_blocks/camera_check_20261030_02/edge_one/raw.jpg'
    source_response = source.with_name('response.json')
    provenance = load(source_response)
    frame_id = provenance['frame_meta']['frame_id']
    raw = source.read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    assert [w, h] == [640, 480]
    (out / 'raw.jpg').write_bytes(raw)
    (out / 'source_response.json').write_bytes(source_response.read_bytes())

    weights = ROOT / 'pc/block_vision/weights/best.pt'
    config = ROOT / 'pc/block_vision/a_top_face_hsv.json'
    detector = YoloBlockDetector(weights, config=config, imgsz=640, conf=.25, iou=.5, min_score=.5)
    result = detector.analyze(image)
    candidates = detector.candidates(result)
    write(out / 'model_result.json', result)
    if not candidates:
        raise RuntimeError('Selected saved frame produced no candidate; inspect full model_result.json')
    chosen = candidates[0]
    face = next(t['top_face'] for t in result['targets'] if t['plugin_output'] == 'candidate' and
                t['raw_label'] == chosen.label)
    payload = chosen.to_dict()
    # Exact production 8099 path. Every board call terminates in this fixture.
    # This fixture provides protocol states only, no pose or calibration data.
    transport_calls = []
    fixture_session = 'OFFLINE_PROTOCOL_FIXTURE_NOT_A_REAL_SESSION'
    def offline_board(path, body=None, timeout=None):
        transport_calls.append(dict(path=path, body=body))
        if path == '/api/v1/mode':
            return dict(armed=True, kind='block_grasp', session_id=fixture_session, min_score=.5)
        if path == '/api/v1/health':
            return dict(mode='real', camera=dict(connected=True), offline_protocol_fixture=True)
        if path == '/api/v1/block/frame':
            return dict(frame_id=frame_id, image_size=[w, h], joints=[],
                        source_type='offline_saved_image_contract_fixture',
                        captured_at=provenance['frame_meta']['captured_at'],
                        jpeg_base64=base64.b64encode(raw).decode())
        if path == '/api/v1/block/target':
            return dict(offline_payload_capture_only=True, no_planning=True, no_motion=True)
        raise AssertionError(path)
    fixture_out = out / 'console_contract_fixture'
    fixture_out.mkdir()
    with patch.object(vision_service, 'board', side_effect=offline_board), patch.dict(
            vision_service.STATE, freeze=None, busy=False, detector=detector,
            args=types.SimpleNamespace(out=fixture_out, max_age=90)):
        preview = vision_service.do_freeze('auto')
        vision_service.do_grasp({k: preview[k] for k in ('freeze_id', 'frame_id', 'session_id')})
    actual_request = next(c['body'] for c in transport_calls if c['path'] == '/api/v1/block/target')
    actual = actual_request['detection']
    target = detection_to_target(actual, [w, h], frame_id, 'pickup', None, 'block')
    without_outline = copy.deepcopy(target)
    without_outline.pop('outline_px')
    stages = dict(locator_quad=face['quad'], plugin_corners=payload['corners'],
                  console_preview_corners=preview['payload']['corners'],
                  console_request_corners=actual['corners'], target_outline=target['outline_px'])
    deltas = {k: float(np.max(np.abs(np.asarray(v) - face['quad']))) for k, v in stages.items()}
    center_deltas = dict(plugin_to_preview=float(np.max(np.abs(np.asarray(payload['center']) - preview['payload']['center']))),
                         preview_to_request=float(np.max(np.abs(np.asarray(preview['payload']['center']) - actual['center']))),
                         request_to_target=float(np.max(np.abs(np.asarray(actual['center']) - target['center_px']))))
    assert all(v == 0 for v in [*deltas.values(), *center_deltas.values()])
    assert target['edge_px'] == actual['corners'][:2]
    assert without_outline['center_px'] == target['center_px'] and without_outline['edge_px'] == target['edge_px']
    exact_center = diagonal_intersection(face['quad'])
    edge = np.array(actual['corners'][1]) - actual['corners'][0]
    audit = dict(source=str(source), source_frame_id=frame_id, source_request_id=provenance['request_id'],
                 source_captured_at_ms=provenance['frame_meta']['captured_at'], raw_sha256=digest(source),
                 image_size=[w, h], model_sha256=digest(weights), hsv_sha256=digest(config),
                 parameters=result['parameters'], python=sys.version, ultralytics=ultralytics.__version__,
                 opencv=cv2.__version__, corners=actual['corners'], center_px=actual['center'],
                 edge_px=target['edge_px'], image_edge_angle_deg=float(np.degrees(np.arctan2(edge[1], edge[0]))),
                 corner_stage_max_abs_delta_px=deltas, center_stage_max_abs_delta_px=center_deltas,
                 diagonal_center_rounding_delta_px=(np.array(actual['center']) - exact_center).tolist(),
                 bbox_center_px=[result['targets'][0]['center_x'], result['targets'][0]['center_y']],
                 used_bbox_center=False, input_resize=False, input_crop=False,
                 model_output_already_raw_camera=True,
                 roi_origin_added_in_locator=True, quad_order_preserved=True,
                 selected_frame_is_failure_scene=False, independent_human_annotation=False,
                 joint_readings=None, field_setup=None, field_arms=None,
                 comparisons=dict(A='pending human corners and matching calibration/pose',
                                  B='pixel inputs identical except presence of outline_px; projection/precheck not evaluated'),
                 transport_fixture_note='In-process mocked transport only. Mode/session are fixtures; no real HTTP and no joint values supplied.')
    write(out / 'pixel_audit.json', audit)
    write(out / 'payload_8099.json', actual)
    write(out / 'transport_fixture.json', dict(offline=True, calls=transport_calls))
    write(out / 'target_with_outline.json', target)
    write(out / 'target_without_outline.json', without_outline)
    write(out / 'annotation_template.json', dict(source_frame_id=frame_id, source_name='edge_one',
          raw_sha256=digest(source), image_size=[w, h], coordinate_space='raw_camera',
          human_confirmed=False, corners=None,
          corner_order='top-left, top-right, bottom-right, bottom-left; clockwise on the visible blue top'))

    canvas = cv2.copyMakeBorder(image, 0, 100, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    quad = np.rint(actual['corners']).astype(np.int32)
    cv2.polylines(canvas, [quad], True, (0, 220, 0), 2)
    for n, p in enumerate(quad, 1):
        cv2.circle(canvas, tuple(p), 4, (0, 0, 255), -1)
        cv2.putText(canvas, str(n), (int(p[0]) + 7, int(p[1]) + 16), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 2)
    cv2.arrowedLine(canvas, tuple(quad[0]), tuple(quad[1]), (255, 80, 0), 2, tipLength=.12)
    center = tuple(round(v) for v in actual['center'])
    cv2.drawMarker(canvas, center, (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
    lines = ['edge_one | ' + frame_id, f'{chosen.label} score={chosen.score:.6f} center={actual["center"]}',
             'Corners 1->2 define direction; numbers are MODEL output, not human labels.',
             'Raw image 640x480 unchanged; footer is display-only.']
    for n, line in enumerate(lines):
        cv2.putText(canvas, line, (5, h + 19 + n * 23), cv2.FONT_HERSHEY_SIMPLEX, .42, (0, 0, 0), 1)
    cv2.imencode('.png', canvas)[1].tofile(str(out / 'ordered_overlay.png'))
    annotation = cv2.copyMakeBorder(image, 0, 48, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    cv2.putText(annotation, 'ANNOTATE: edge_one | ' + frame_id, (5, h + 20), cv2.FONT_HERSHEY_SIMPLEX, .42, (0, 0, 0), 1)
    cv2.putText(annotation, 'Original image above: 640x480; no model marks.', (5, h + 40), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 0), 1)
    cv2.imencode('.png', annotation)[1].tofile(str(out / 'annotation_input.png'))
    make_annotation_page(out, raw, frame_id, digest(source))
    print(json.dumps(dict(output=str(out), json_scanned=inv['json_counts'],
                          planning_requests=len(inv['planning_requests']), freeze_directories=len(inv['freeze_directories']),
                          frame_id=frame_id, payload=actual, max_coordinate_delta_px=max(deltas.values()),
                          projection_or_collision_evaluated=False), ensure_ascii=False))


def make_annotation_page(out, raw, frame_id, raw_hash):
    template = Path(__file__).with_name('offline_edge_annotation.html').read_text(encoding='utf-8')
    metadata = dict(source_name='edge_one', source_frame_id=frame_id, raw_sha256=raw_hash,
                    image_size=[640, 480], coordinate_space='raw_camera')
    html = template.replace('__META_JSON__', json.dumps(metadata)).replace('__JPEG_BASE64__', base64.b64encode(raw).decode())
    (out / 'annotate_edge_one.html').write_text(html, encoding='utf-8')


if __name__ == '__main__':
    main()
