"""Download pinned official model, check hash, and never overwrite changed assets."""
import hashlib
import urllib.request
from emotion_interaction.smile_detector import MODEL_PATH, MODEL_SHA256, MODEL_URL


def main():
    if MODEL_PATH.exists():
        if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Existing face model hash mismatch; refusing overwrite")
    else:
        print("Downloading official Face Landmarker model...", flush=True)
        with urllib.request.urlopen(MODEL_URL, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise ValueError("Downloaded face model hash mismatch")
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_bytes(data)
    print(f"Verified model: {MODEL_PATH}")


if __name__ == "__main__": main()
