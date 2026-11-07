"""Pinned D weights: verify before use; no hardware or robot client imports."""
import hashlib
from pathlib import Path

WEIGHTS = Path(__file__).resolve().parent / 'weights'
COMMIT = 'f13db616a66f521882fac2d6c02322854ebb1343'
ASSETS = {
    'best_hand_gesture_classifier.pt': (
        f'https://raw.githubusercontent.com/SirAlexiner/HandGestureRecognition/{COMMIT}/best_hand_gesture_classifier.pt',
        '121ca234159eecb18c1b7c858285db8048d0157cc87bc3ddaedbda6d20d09298'),
    'hand_landmarker.task': (
        'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
        'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'),
    'face_landmarker.task': (
        'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
        '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'),
}


def verify_asset(name, weights=WEIGHTS):
    path = Path(weights) / name
    if not path.is_file():
        raise FileNotFoundError(f'{path}: run .venv/Scripts/python.exe pc/gesture/download_assets.py')
    if hashlib.sha256(path.read_bytes()).hexdigest() != ASSETS[name][1]:
        raise ValueError(f'MODEL_HASH_MISMATCH: {path}')
    return path
