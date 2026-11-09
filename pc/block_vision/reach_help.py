"""画一张"可达范围"图：用板端 /manipulation/capture 拿同一帧 + 可达环，并叠加 A 识别结果。

只读入口：capture() 只读关节、取一帧、算模型环，不下发任何运动，也不需要模式会话。
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from block_vision.yolo_detector import YoloBlockDetector  # noqa: E402

BASE = 'http://192.168.137.100:8080'
OUT = Path(r'G:\robotic-manipulator-integration\pc\block_vision\weights\field_20261109_214803\analysis_lab')


def request(path, body=None, timeout=40):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method='GET' if body is None else 'POST',
                                 headers={'Content-Type': 'application/json', 'Origin': BASE})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='block_yellow')
    args = parser.parse_args()

    csrf = request('/control/status')['csrf']
    cap = request('/manipulation/capture', {'csrf': csrf})
    raw = base64.b64decode(cap['jpeg_base64'])
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit('capture 返回的不是有效 JPEG')

    overlay = cap.get('reach_overlay') or {}
    radii = overlay.get('radii_mm') or []
    lines = overlay.get('lines') or []
    canvas = image.copy()
    colours = [(0, 200, 0), (0, 128, 255), (0, 0, 255), (255, 0, 255)]
    for index, line in enumerate(lines):
        pts = np.rint(np.asarray(line['points'], np.float64)).astype(np.int32)
        if len(pts) < 2:
            continue
        colour = colours[index % len(colours)]
        cv2.polylines(canvas, [pts], False, colour, 2)
        mid = pts[len(pts) // 2]
        cv2.putText(canvas, f"{line['radius_mm']:.0f}mm", (int(mid[0]) + 4, int(mid[1]) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, colour, 1)

    detector = YoloBlockDetector()
    result = detector.analyze(image)
    candidates = detector.candidates(result)
    chosen = next((d for d in sorted(candidates, key=lambda d: d.score, reverse=True)
                   if d.label == args.label), None)
    for target in result['targets']:
        face = target['top_face']
        if face.get('quad'):
            cv2.polylines(canvas, [np.rint(np.asarray(face['quad'])).astype(np.int32)], True, (0, 255, 255), 1)
        if face.get('candidate_point'):
            p = face['candidate_point']
            cv2.drawMarker(canvas, (round(p['x']), round(p['y'])), (255, 0, 255), cv2.MARKER_CROSS, 18, 2)
    if chosen:
        cv2.putText(canvas, f'{chosen.label} {chosen.score:.3f} center=({chosen.center[0]:.0f},{chosen.center[1]:.0f})',
                    (6, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 2)
        cv2.putText(canvas, f'{chosen.label} {chosen.score:.3f} center=({chosen.center[0]:.0f},{chosen.center[1]:.0f})',
                    (6, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%H%M%S')
    path = OUT / f'reach_{stamp}.png'
    cv2.imwrite(str(path), canvas)
    (OUT / f'reach_{stamp}.json').write_text(json.dumps(
        dict(ids=cap.get('id'), joints=cap.get('joints'), radii_mm=radii,
             lines=[{'radius_mm': l['radius_mm'], 'points': len(l['points'])} for l in lines],
             detection=chosen.to_dict() if chosen else None,
             captured_at=cap.get('captured_at'), pc_time=datetime.now(timezone.utc).isoformat()),
        ensure_ascii=False, indent=2), encoding='utf-8')

    print('radii_mm =', radii)
    print('lines    =', [(l['radius_mm'], len(l['points'])) for l in lines])
    print('block    =', None if not chosen else (chosen.label, round(chosen.score, 6),
                                                (round(chosen.center[0], 1), round(chosen.center[1], 1))))
    print('image    =', path)


if __name__ == '__main__':
    main()
