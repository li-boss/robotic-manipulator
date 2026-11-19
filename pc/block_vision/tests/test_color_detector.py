"""示例颜色检测器只使用合成图像，不连接开发板。"""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

PC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PC))

from block_vision.color_detector import ColorBlockDetector  # noqa: E402


class ColorDetectorTests(unittest.TestCase):
    def test_color_detector_finds_rotated_square(self):
        image = np.full((480, 640, 3), 40, np.uint8)
        box = cv2.boxPoints(((320, 240), (60, 60), 30)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (40, 210, 245))
        found = ColorBlockDetector().detect(image)
        self.assertEqual(len(found), 1)
        np.testing.assert_allclose(np.mean(found[0].corners, axis=0), [320, 240], atol=2)
        self.assertEqual(ColorBlockDetector().detect(np.zeros((480, 640, 3), np.uint8)), [])


if __name__ == '__main__':
    unittest.main()
