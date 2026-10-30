"""Reproduce pinned upstream assets without committing weights or modifying upstream."""
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT=Path(__file__).resolve().parents[1]/"emotion_interaction"/"models"
COMMIT="f13db616a66f521882fac2d6c02322854ebb1343"
MODEL_HASH="121ca234159eecb18c1b7c858285db8048d0157cc87bc3ddaedbda6d20d09298"
HAND_HASH="fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def fetch(url):
    with urllib.request.urlopen(url,timeout=120) as response:
        return response.read()


def write_asset(path,data):
    if path.exists():
        if path.read_bytes()!=data:
            raise ValueError(f"Refusing to overwrite changed asset: {path}")
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)


def main():
    upstream=ROOT/"HandGestureRecognition-main"
    model=upstream/"best_hand_gesture_classifier.pt"
    if not model.exists():
        archive=zipfile.ZipFile(io.BytesIO(fetch(f"https://github.com/SirAlexiner/HandGestureRecognition/archive/{COMMIT}.zip")))
        for entry in archive.infolist():
            if entry.is_dir(): continue
            parts=PurePosixPath(entry.filename).parts[1:]
            path=upstream.joinpath(*parts).resolve()
            if not path.is_relative_to(upstream.resolve()):
                raise ValueError("Unsafe archive member")
            write_asset(path,archive.read(entry))
    if hashlib.sha256(model.read_bytes()).hexdigest()!=MODEL_HASH:
        raise ValueError("Classification weight hash mismatch")
    hand=ROOT/"hand_landmarker.task"
    if not hand.exists():
        data=fetch("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
        if hashlib.sha256(data).hexdigest()!=HAND_HASH:
            raise ValueError("Hand landmark weight hash mismatch")
        write_asset(hand,data)
    if hashlib.sha256(hand.read_bytes()).hexdigest()!=HAND_HASH:
        raise ValueError("Existing Hand landmark weight hash mismatch")
    print(f"Verified assets: {ROOT}")


if __name__=="__main__": main()
