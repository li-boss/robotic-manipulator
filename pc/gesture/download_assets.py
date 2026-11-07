"""Download verified weights, or copy the identical local weights from the old D folder."""
import argparse
import hashlib
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gesture.assets import ASSETS, WEIGHTS, verify_asset


def install(weights=WEIGHTS, local=None):
    weights = Path(weights)
    for name, (url, expected) in ASSETS.items():
        target = weights / name
        if target.exists():
            verify_asset(name, weights)
            print(f'Verified: {target}', flush=True)
            continue
        source = Path(local) / name if local else None
        if source is not None and name.endswith('.pt'):
            source = Path(local) / 'HandGestureRecognition-main' / name
        if source is not None:
            data = source.read_bytes()
        else:
            print(f'Downloading: {url}', flush=True)
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'MODEL_HASH_MISMATCH: {name}; destination not written')
        weights.mkdir(parents=True, exist_ok=True)
        # Never overwrite an existing or changed weight.
        with target.open('xb') as output:
            output.write(data)
        print(f'Installed and verified: {target}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-local', type=Path, help='Old emotion_interaction/models directory; no network')
    args = parser.parse_args()
    install(local=args.from_local)
