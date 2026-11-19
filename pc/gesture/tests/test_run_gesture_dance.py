"""手势运行器的防抖与动作映射测试，不连接开发板。"""
from pathlib import Path
import sys
import unittest

PC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PC))

from armlink import GestureEvent  # noqa: E402
from gesture.run_gesture_dance import Debouncer, parse_map  # noqa: E402


class RunnerTests(unittest.TestCase):
    def test_debouncer_and_map(self):
        d = Debouncer(stable=3, threshold=.6)
        seq = [GestureEvent('smile', .9)] * 5 + [None, GestureEvent('smile', .9), GestureEvent('smile', .5)] + [GestureEvent('wave', .9)] * 3
        fired = [e.label for e in map(d.feed, seq) if e]
        self.assertEqual(fired, ['smile', 'wave'])
        self.assertEqual(parse_map('smile=sway, wave = circle'), dict(smile='sway', wave='circle'))
        with self.assertRaises(SystemExit):
            parse_map('smile')


if __name__ == '__main__':
    unittest.main()
