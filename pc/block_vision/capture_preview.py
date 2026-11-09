"""Capture and analyze one frame, then exit. No target submission or mode changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armlink import BoardClient, BoardError
from block_vision.preview import overlay
from block_vision.yolo_detector import YoloBlockDetector


def first_mjpeg(stream, max_bytes=8 * 1024 * 1024, max_seconds=8):
    """Extract a complete JPEG; EOF/timeout never becomes a reused old frame."""
    buffer = b''
    started = time.monotonic()
    while time.monotonic() - started < max_seconds:
        chunk = stream.read1(65536)
        if not chunk:
            raise RuntimeError('MJPEG stream ended before a complete frame')
        buffer += chunk
        if len(buffer) > max_bytes:
            raise RuntimeError('MJPEG frame exceeds bounded buffer')
        begin = buffer.find(b'\xff\xd8')
        end = buffer.find(b'\xff\xd9', begin + 2) if begin >= 0 else -1
        if begin >= 0 and end >= 0:
            return buffer[begin:end + 2]
    raise TimeoutError('no complete JPEG within acquisition deadline')


def capture_frame(client, base, *, freeze=False, opener=None):
    """Reuse BoardClient for the new protocol; legacy uses GET only.

    freeze requires an ALREADY active block session, never enables one. The
    frozen image, frame_id and joints all come from the same client response.
    """
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    metadata = dict(receive_started_at_pc=datetime.now(timezone.utc).isoformat(),
                    frame_id=None, source_exposure_timestamp=None)
    try:
        health = client.health()
    except BoardError as error:
        if error.status != 404 or freeze:
            raise
        with opener.open(base + '/status', timeout=5) as response:
            status = json.load(response)
        if not status.get('connected'):
            raise RuntimeError('legacy camera unavailable: ' + str(status.get('message')))
        with opener.open(base + '/stream.mjpg', timeout=5) as response:
            raw = first_mjpeg(response)
        metadata.update(source_type='legacy_camera_service_mjpeg',
                        source_url=base + '/stream.mjpg', camera_status=status)
    else:
        metadata['board_health'] = health
        if health.get('api_version') != 1 or health.get('service') != 'mechanical-arm-board':
            raise RuntimeError('unrecognized board protocol; do not infer frame identity')
        if not health.get('camera', {}).get('connected'):
            raise RuntimeError('new board camera unavailable')
        if freeze:
            mode = client.mode()
            if not (mode.get('armed') and mode.get('kind') == 'block_grasp' and
                    mode.get('session_id') and not mode.get('busy') and not health.get('busy')):
                raise RuntimeError('freeze requires an existing idle block_grasp session; no mode was changed')
            frame = client.freeze_frame(mode['session_id'])
            if not frame.frame_id:
                raise ValueError('frozen response has no frame_id')
            raw = frame.jpeg
            metadata.update(source_type='new_board_frozen_frame', frame_id=frame.frame_id,
                            board_captured_at=frame.captured_at, joints=frame.joints,
                            image_size=list(frame.image_size))
        else:
            raw = client.snapshot()
            metadata.update(source_type='new_board_snapshot_without_freeze',
                            source_url=base + '/api/v1/frame.jpg')
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('camera response is not a valid image')
    height, width = image.shape[:2]
    if freeze and metadata['image_size'] != [width, height]:
        raise ValueError('frozen frame metadata differs from decoded original size')
    metadata.update(receive_finished_at_pc=datetime.now(timezone.utc).isoformat(),
                    raw_sha256=hashlib.sha256(raw).hexdigest(), image_size=[width, height],
                    time_note='PC receive time is not board exposure time; no source timestamp is invented.')
    return raw, image, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', default='http://192.168.137.100:8080')
    parser.add_argument('--out', required=True, help='new local output directory')
    parser.add_argument('--scene', default='unconfirmed_current_scene')
    parser.add_argument('--freeze', action='store_true',
                        help='freeze once in an existing idle block session, then save and exit')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # Only affects this preview process and ensures private-board calls bypass proxies.
    urllib.request.install_opener(opener)
    record = dict(scene=args.scene, human_confirmed=False, freeze_requested=args.freeze,
                  board=args.board.rstrip('/'), status='not_captured')
    try:
        raw, image, capture = capture_frame(BoardClient(record['board'], timeout=5),
                                             record['board'], freeze=args.freeze, opener=opener)
        record.update(capture, status='captured')
        (out / 'raw.jpg').write_bytes(raw)
        # Use the same BGR decode, without fetching another frame or changing IDs.
        detector = YoloBlockDetector()
        result = detector.analyze(image)
        result.update(frame_id=capture['frame_id'], raw_sha256=capture['raw_sha256'],
                      source_type=capture['source_type'],
                      plugin_detections=[d.to_dict() for d in detector.candidates(result)])
        (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        success, encoded = cv2.imencode('.png', overlay(image, result))
        if not success:
            raise RuntimeError('overlay encoding failed')
        encoded.tofile(str(out / 'overlay.png'))
        record.update(status='analyzed', yolo_boxes=len(result['targets']),
                      candidates=len(result['plugin_detections']), overlay=str((out / 'overlay.png').resolve()))
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__, error=str(error))
    (out / 'capture.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))
    if record['status'] != 'analyzed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
