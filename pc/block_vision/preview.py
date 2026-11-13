"""Offline original-image preview. This command never opens a network connection."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from block_vision.yolo_detector import YoloBlockDetector


def overlay(image, result):
    shown = image.copy()
    for index, target in enumerate(result['targets'], 1):
        x1, y1, x2, y2 = map(round, target['bbox'])
        cv2.rectangle(shown, (x1, y1), (x2, y2), (0, 255, 255), 1)
        face = target['top_face']
        for key, color in [('visible_contour', (0, 170, 255)), ('quad', (0, 220, 0))]:
            if face.get(key):
                cv2.polylines(shown, [np.rint(face[key]).astype(np.int32)], True, color, 1)
        if target['plugin_output'] == 'candidate':
            p = face['candidate_point']
            cv2.drawMarker(shown, (round(p['x']), round(p['y'])), (255, 0, 255),
                           cv2.MARKER_CROSS, 16, 2)
        # Text goes below the image to keep the top face visible.
    canvas = cv2.copyMakeBorder(shown, 0, max(24, len(result['targets']) * 24),
                               0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    for index, target in enumerate(result['targets']):
        face = target['top_face']
        text = f"{index+1} {target['raw_label']} {target['confidence']:.2f} {target['plugin_output']}"
        if face.get('reason'):
            text += ' ' + face['reason']
        cv2.putText(canvas, text, (4, image.shape[0] + 17 + index * 24),
                    cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 0), 1)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='one local original image')
    parser.add_argument('--out', required=True, help='new evidence directory, must not exist')
    parser.add_argument('--weights')
    args = parser.parse_args()
    source = Path(args.source)
    raw = source.read_bytes()
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('invalid source image')
    out = Path(args.out)
    if out.exists():
        raise FileExistsError(out)
    detector = YoloBlockDetector(args.weights)
    result = detector.analyze(image)
    result.update(source_type='saved_image_development_replay', source=str(source.resolve()),
                  raw_sha256=hashlib.sha256(raw).hexdigest(),
                  original_capture_time=None, warmup_ms=detector.warmup_ms,
                  plugin_detections=[d.to_dict() for d in detector.candidates(result)])
    out.mkdir(parents=True)
    (out / ('raw' + source.suffix)).write_bytes(raw)
    cv2.imencode('.png', overlay(image, result))[1].tofile(str(out / 'overlay.png'))
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(targets=len(result['targets']), candidates=len(result['plugin_detections']),
                         simulated=result['simulated'], output=str(out.resolve())), ensure_ascii=False))


if __name__ == '__main__':
    main()
