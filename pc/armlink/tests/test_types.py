"""电脑端与开发板之间的数据结构测试。"""
from pathlib import Path
import sys
import unittest

import numpy as np

PC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PC))

from armlink import BlockDetection  # noqa: E402


class TypesTests(unittest.TestCase):
    def test_block_detection_serialization(self):
        d = BlockDetection(corners=np.array([[1, 2], [3, 2], [3, 4], [1, 4]]), score=np.float32(.7), label='yellow')
        self.assertEqual(d.to_dict(), dict(score=.699999988079071, corners=[[1, 2], [3, 2], [3, 4], [1, 4]], label='yellow'))
        self.assertEqual(BlockDetection(bbox=[1, 2, 30, 40], angle_deg=15).to_dict()['angle_deg'], 15.0)
        with self.assertRaises(ValueError):
            BlockDetection().to_dict()
        with self.assertRaises(ValueError):
            BlockDetection(corners=[[0, 0]] * 3).to_dict()


if __name__ == '__main__':
    unittest.main()
