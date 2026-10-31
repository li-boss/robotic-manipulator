
"""OpenCV is used only to decode/capture; no arm control belongs here."""
from __future__ import annotations

from pathlib import Path
from struct import unpack
from threading import Event, Thread

from camera_service.camera import FrameStore


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV missing: install the version approved for the target environment") from exc


def image_size(data: bytes) -> tuple[int, int]:
    """Read PNG/JPEG dimensions; reject files without a valid image header."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("only JPEG and PNG images are supported")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xff:
            raise ValueError("invalid JPEG marker")
        marker = data[pos + 1]
        pos += 2
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        if pos + 2 > len(data):
            break
        length = unpack(">H", data[pos:pos + 2])[0]
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 7:
                raise ValueError("invalid JPEG size marker")
            height, width = unpack(">HH", data[pos + 3:pos + 7])
            return width, height
        pos += length
    raise ValueError("JPEG dimensions not found")


class CaptureWorker:
    def __init__(self, store: FrameStore, mode: str, source: str,
                 fps_limit: float = 10.0):
        if mode not in {"image", "video", "camera"}:
            raise ValueError("mode must be image, video, or camera")
        self.store, self.mode, self.source = store, mode, source
        self.fps_limit = fps_limit
        self.error: str | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="camera-owner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _publish(self, cv2, image) -> None:
        if image is None or image.size == 0:
            raise ValueError("invalid decoded image")
        height, width = image.shape[:2]
        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            raise ValueError("JPEG encoding failed")
        self.store.publish(encoded.tobytes(), width, height)

    def _run(self) -> None:
        try:
            if self.mode == "image":
                path = Path(self.source)
                if not path.is_file():
                    raise FileNotFoundError(path)
                data = path.read_bytes()
                width, height = image_size(data)
                if width < 1 or height < 1:
                    raise ValueError("zero-size image")
                if data.startswith(b"\xff\xd8"):
                    self.store.publish(data, width, height)
                else:
                    cv2 = _cv2()
                    image = cv2.imdecode(__import__("numpy").frombuffer(data, dtype="uint8"), cv2.IMREAD_COLOR)
                    self._publish(cv2, image)
                return
            cv2 = _cv2()
            if self.mode == "video" and not Path(self.source).is_file():
                raise FileNotFoundError(self.source)
            is_http_mjpeg = self.mode == "camera" and self.source.startswith(("http://", "https://"))
            if self.mode == "camera":
                # A board-side camera service owns /dev/video0. Read its MJPEG
                # stream here without opening the same USB device again.
                device = self.source if self.source.startswith(("http://", "https://")) else int(self.source)
            else:
                device = self.source
            while not self._stop.is_set():
                if is_http_mjpeg:
                    # Open/read timeouts are constructor-only FFmpeg properties.
                    # On timeout, release this connection and mark the frame
                    # source unavailable before reconnecting; never republish
                    # the last successfully decoded image with a new frame ID.
                    capture = cv2.VideoCapture(
                        device, cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])
                else:
                    capture = cv2.VideoCapture(device)
                try:
                    if not capture.isOpened():
                        self.store.mark_unavailable()
                        self.error = "CAMERA_UNAVAILABLE"
                        if self.mode == "video":
                            return
                        self._stop.wait(0.5)
                        continue
                    while not self._stop.is_set():
                        ok, image = capture.read()
                        if not ok:
                            self.store.mark_unavailable()
                            self.error = "VIDEO_ENDED" if self.mode == "video" else "CAMERA_UNAVAILABLE"
                            break
                        self._publish(cv2, image)
                        self.error = None
                        # Pacing an HTTP MJPEG reader lets FFmpeg/network data
                        # accumulate while this owner sleeps. Drain every new
                        # decoded frame; FrameStore retains only a bounded cache.
                        if self.fps_limit > 0 and not is_http_mjpeg:
                            self._stop.wait(1 / self.fps_limit)
                finally:
                    capture.release()
                if self.mode == "video":
                    return
                self._stop.wait(0.5)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.store.mark_unavailable()
