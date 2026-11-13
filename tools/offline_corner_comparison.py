"""Compare a user's raw-image quad to a saved model quad; no network or robot."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def forbid(*args, **kwargs):
    raise RuntimeError('Offline comparison cannot connect to network or hardware')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--human', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    socket.socket.connect = socket.socket.connect_ex = forbid
    socket.create_connection = forbid
    hardware = types.ModuleType('Arm_Lib')
    hardware.Arm_Device = forbid
    sys.modules['Arm_Lib'] = hardware
    sys.path[:0] = [str(ROOT / 'pc'), str(ROOT / 'board')]
    import cv2
    import numpy as np
    from block_vision.top_face import diagonal_intersection
    from modes import detection_to_target

    evidence, out = args.evidence.resolve(), args.out.resolve()
    human = read(args.human)
    audit = read(evidence / 'pixel_audit.json')
    payload = read(evidence / 'payload_8099.json')
    raw = (evidence / 'raw.jpg').read_bytes()
    for key in ('source_frame_id', 'raw_sha256', 'image_size', 'coordinate_space'):
        expected = audit.get(key, 'raw_camera' if key == 'coordinate_space' else None)
        if human.get(key) != expected:
            raise ValueError('Human annotation provenance mismatch: ' + key)
    if hashlib.sha256(raw).hexdigest() != human['raw_sha256'] or human.get('human_confirmed') is not True:
        raise ValueError('Raw-image hash or human confirmation is invalid')
    model = np.array(payload['corners'], dtype=float)
    native = np.array(human['corners'], dtype=float)
    width, height = human['image_size']
    if (native.shape != (4, 2) or not np.isfinite(native).all() or
            np.any(native < 0) or np.any(native >= [width, height]) or
            not cv2.isContourConvex(native.astype(np.float32))):
        raise ValueError('Human corners must be a convex quad inside the original image')
    # Match corresponding physical corners with only contour-preserving changes.
    permutations = [list(range(4))[::direction][shift:] + list(range(4))[::direction][:shift]
                    for direction in (1, -1) for shift in range(4)]
    indices = min(permutations, key=lambda order: float(np.sum((native[order] - model) ** 2)))
    aligned = native[indices]
    delta = model - aligned  # Positive X right, positive Y down.
    distances = np.linalg.norm(delta, axis=1)
    model_center_exact = np.array(diagonal_intersection(model.tolist()))
    human_center_exact = np.array(diagonal_intersection(native.tolist()))
    model_center = np.round(model_center_exact, 3)
    human_center = np.round(human_center_exact, 3)
    assert model_center.tolist() == payload['center']
    def angle(points):
        d = points[1] - points[0]
        return float(np.degrees(np.arctan2(d[1], d[0])))
    def mapped(corners, center):
        p = copy.deepcopy(payload)
        p.update(corners=corners.tolist(), center=center.tolist())
        return p, detection_to_target(p, [width, height], human['source_frame_id'], 'pickup', None, 'block')
    model_payload, model_target = mapped(model, model_center)
    human_payload, human_target = mapped(aligned, human_center)
    native_payload, native_target = mapped(native, human_center)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'human_input.json').write_bytes(args.human.read_bytes())
    (out / 'raw.jpg').write_bytes(raw)
    for name, p, t in [('model', model_payload, model_target), ('human_aligned', human_payload, human_target),
                       ('human_native', native_payload, native_target)]:
        write(out / (name + '_payload.json'), p)
        write(out / (name + '_target_with_outline.json'), t)
        without = copy.deepcopy(t)
        del without['outline_px']
        write(out / (name + '_target_without_outline.json'), without)
        assert t['center_px'] == without['center_px'] and t['edge_px'] == without['edge_px']
    comparison = dict(
        source_frame_id=human['source_frame_id'], raw_sha256=human['raw_sha256'], human_confirmed=True,
        corner_difference_convention='model minus corresponding human corner; X right/Y down; pixels',
        model_corners=model.tolist(), human_native_corners=native.tolist(),
        human_indices_aligned_to_model=[i + 1 for i in indices],
        corner_deltas_px=delta.tolist(), corner_errors_px=distances.tolist(),
        corner_mean_error_px=float(np.mean(distances)), corner_max_error_px=float(np.max(distances)),
        corner_rmse_px=float(np.sqrt(np.mean(distances ** 2))),
        model_center_px=model_center.tolist(), human_center_px=human_center.tolist(),
        center_delta_px=(model_center - human_center).tolist(),
        center_error_px=float(np.linalg.norm(model_center - human_center)),
        exact_diagonal_center_delta_px=(model_center_exact - human_center_exact).tolist(),
        image_angles_deg=dict(model=angle(model), human_native=angle(native), human_same_physical_edge=angle(aligned)),
        same_physical_edge_angle_delta_deg=angle(model) - angle(aligned),
        center_policy='Both derived from quad diagonal intersection, rounded to 0.001 pixels as in production locator',
        alignment_policy='Comparison only; human source and production model order unchanged',
        fixed_fields_note='Same score/label/frame/task for controlled comparison. Retained score is a constant, not a human confidence estimate.',
        comparison_A=dict(first_difference='input quad pixels', downstream_projection=None, collision_precheck=None),
        comparison_B=dict(only_changed_field='outline_px', centers_and_edge_endpoints_identical=True,
                          downstream_projection=None, collision_precheck=None),
        model_to_8099_transport_delta_px=0.0, scene_joint_angles=None, scene_calibration=None,
        fault_230mm_reproduced=False, production_code_changed=False)
    write(out / 'comparison.json', comparison)
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    model_color, human_color = (255, 160, 0), (0, 110, 255)
    def draw(canvas, origin=(0, 0), scale=1):
        def xy(p):
            return tuple(np.rint((np.asarray(p) - origin) * scale).astype(int))
        for quad, center, color in ((model, model_center, model_color), (aligned, human_center, human_color)):
            cv2.polylines(canvas, [np.array([xy(p) for p in quad])], True, color, 2)
            cv2.drawMarker(canvas, xy(center), color, cv2.MARKER_CROSS, 18, 2)
            cv2.arrowedLine(canvas, xy(quad[0]), xy(quad[1]), color, 2, tipLength=.12)
    canvas = cv2.copyMakeBorder(image, 0, 100, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    draw(canvas)
    for n, (text, color) in enumerate([
            ('edge_one | ' + human['source_frame_id'], (0, 0, 0)),
            ('BLUE: model; ORANGE: user annotation. Arrows use the SAME physical edge.', (0, 0, 0)),
            (f'Corner max={max(distances):.3f}px; center difference={comparison["center_error_px"]:.3f}px', (0, 0, 0)),
            ('Pixel comparison only: no calibrated projection or collision result.', (0, 0, 0))]):
        cv2.putText(canvas, text, (5, height + 19 + 23 * n), cv2.FONT_HERSHEY_SIMPLEX, .42, color, 1)
    cv2.imencode('.png', canvas)[1].tofile(str(out / 'comparison_overlay.png'))
    origin, scale = (365, 0), 4
    zoom = cv2.resize(image[0:150, 365:550], None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    draw(zoom, origin, scale)
    zoom = cv2.copyMakeBorder(zoom, 0, 48, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    cv2.putText(zoom, 'Display crop x=365..549 y=0..149, 4x; coordinates in JSON remain raw pixels.',
                (5, 622), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 0), 1)
    cv2.imencode('.png', zoom)[1].tofile(str(out / 'comparison_zoom.png'))
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
