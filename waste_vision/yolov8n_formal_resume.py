"""Resume an interrupted formal CPU training run from its own last.pt."""
from __future__ import annotations
import csv, json, time
from datetime import datetime, timezone
from pathlib import Path
import os
os.environ.setdefault("YOLO_CONFIG_DIR",str(Path("data/a_blocks/cache/ultralytics").resolve()))
from ultralytics import YOLO
from waste_vision.yolov8n_formal_train import CLASSES, sha, verify_data

RUN = Path('data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01')
DATA = Path('data/a_blocks/formal_v1_20261022')
CLASSES_FILE = Path('configs/a_confirmed_classes.txt')

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    info_path = RUN / 'formal_train_result.json'
    info = json.loads(info_path.read_text(encoding='utf-8'))
    if info['status'] not in ('running', 'interrupted') or info['device'] != 'cpu':
        raise ValueError(f"run is not an interrupted CPU run: {info['status']}")
    manifest, _ = verify_data(DATA, CLASSES_FILE)
    if sha(DATA / 'split_manifest.json') != info['data_manifest_sha256']:
        raise ValueError('dataset changed since initial training')
    if manifest['image_counts'] != info['image_counts']:
        raise ValueError('image counts changed since initial training')
    last = RUN / 'weights' / 'last.pt'
    if not last.is_file():
        raise FileNotFoundError(last)
    previous = list(csv.DictReader((RUN / 'results.csv').open(encoding='utf-8')))
    if not previous:
        raise ValueError('no completed epoch to resume')
    info['status'] = 'resuming'
    info['resume_started_at'] = now()
    info['resumed_from_epoch'] = len(previous)
    info['resume_checkpoint_sha256'] = sha(last)
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    start = time.perf_counter()
    try:
        YOLO(str(last.resolve())).train(resume=True, device='cpu', workers=0, plots=True)
        best = RUN / 'weights' / 'best.pt'
        if not best.is_file() or not last.is_file():
            raise FileNotFoundError('best.pt or last.pt missing after resume')
        names = tuple(YOLO(str(best)).names[i] for i in range(4))
        if names != CLASSES:
            raise ValueError(f'class order changed: {names}')
        rows = list(csv.DictReader((RUN / 'results.csv').open(encoding='utf-8')))
        info.update(status='completed', completed_at=now(), resume_elapsed_s=time.perf_counter()-start,
                    actual_epochs_logged=len(rows), best_pt=str(best.resolve()), best_sha256=sha(best),
                    last_pt=str(last.resolve()), last_sha256=sha(last),
                    last_training_row=rows[-1] if rows else None)
    except Exception as exc:
        info.update(status='failed', completed_at=now(), resume_elapsed_s=time.perf_counter()-start,
                    error_type=type(exc).__name__, error=str(exc))
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': info['status'], 'epochs': info['actual_epochs_logged'],
                      'best_pt': info['best_pt']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
