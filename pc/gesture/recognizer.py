"""D single-frame adapters for B's GestureRecognizer interface; no robot transport.

Labels: fist, like, ok, palm, smile. A qualifying hand takes priority over smile.
The runner owns confirmation, release and cooldown. Scores are passed unchanged.
"""
import math
import numpy as np

from armlink import GestureEvent
from .base import GestureRecognizer
from .assets import WEIGHTS

HAND_LABELS = frozenset(('fist', 'like', 'ok', 'palm'))


def valid_frame(image):
    return (isinstance(image, np.ndarray) and image.dtype == np.uint8
            and image.ndim == 3 and image.shape[2] == 3 and image.size > 0)


def score_ok(value, threshold):
    return (type(value) in (int, float) and math.isfinite(value)
            and threshold <= value <= 1)


class CombinedRecognizer(GestureRecognizer):
    def __init__(self, *, weights=WEIGHTS, hand_threshold=0.8, smile_threshold=0.3,
                 hand_detector=None, smile_detector=None, mode='combined'):
        if mode not in ('combined', 'gesture', 'smile'):
            raise ValueError('mode must be combined / gesture / smile')
        for threshold in (hand_threshold, smile_threshold):
            if not score_ok(threshold, 0):
                raise ValueError('threshold must be a finite number from 0 to 1')
        self.hand_threshold, self.smile_threshold = hand_threshold, smile_threshold
        self.hand = self.face = None
        self.last = {}
        self.closed = False
        try:
            if mode != 'smile':
                from .gesture_detector import GestureDetector
                self.hand = hand_detector if hand_detector is not None else GestureDetector(weights=weights)
            if mode != 'gesture':
                from .smile_detector import SmileDetector
                self.face = smile_detector if smile_detector is not None else SmileDetector(weights=weights)
        except Exception:
            self.close()
            raise

    def recognize(self, image):
        self.last = {}
        if self.closed:
            raise RuntimeError('recognizer is closed')
        if not valid_frame(image):
            return None
        # Each detector sees the same full, unannotated BGR frame.
        hand = self.hand.detect(image) if self.hand is not None else {}
        face = self.face.detect(image) if self.face is not None else {}
        self.last = dict(hand=hand, face=face)
        if (hand.get('hand_count') == 1 and hand.get('gesture') in HAND_LABELS
                and score_ok(hand.get('confidence'), self.hand_threshold)):
            return GestureEvent(hand['gesture'], float(hand['confidence']))
        if (face.get('face_count') == 1 and face.get('status') == 'FACE_DETECTED'
                and score_ok(face.get('smile_score'), self.smile_threshold)):
            return GestureEvent('smile', float(face['smile_score']))
        return None

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.hand is not None:
                self.hand.close()
        finally:
            if self.face is not None:
                self.face.close()


class HandRecognizer(CombinedRecognizer):
    def __init__(self, **kwargs):
        super().__init__(mode='gesture', **kwargs)


class SmileRecognizer(CombinedRecognizer):
    def __init__(self, **kwargs):
        super().__init__(mode='smile', **kwargs)


class DanceRecognizer(CombinedRecognizer):
    """Production dance preset with a slightly more permissive smile cutoff."""

    def __init__(self, **kwargs):
        kwargs.setdefault('smile_threshold', 0.2)
        super().__init__(**kwargs)
