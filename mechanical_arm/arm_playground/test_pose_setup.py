from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import arm
import pose_setup


class Fake:
    real = True
    def read_report(self):
        return [dict(id=i, angle=89 if i == 5 else 90, read_ok=True, error=None,
                     within_motion_hard_limits=i != 5) for i in range(1, 7)]


class Tests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.cfg = dict(hardware_ready=True, motion_ms=1500, settle_ms=300,
                        joint_limits={str(i): list(v) for i, v in arm.HARD_LIMITS.items()},
                        poses={}, gripper={}, targets={})
        self.path = self.root / 'config.json'
        self.path.write_text(json.dumps(self.cfg))
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.patch = patch('pose_setup.ROOT', self.root)
        self.patch.start()
    def tearDown(self):
        self.patch.stop()
        self.output.__exit__(None, None, None)
        self.folder.cleanup()
    def test_raw_89_draft_saved_but_not_motion_config(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            pose_setup.capture(Fake(), self.cfg, self.path, 'ready', ask=lambda _: 'y')
        self.assertEqual(self.path.read_bytes(), original)
        sample = json.loads((self.root / 'logs/pose_samples.jsonl').read_text().splitlines()[0])
        self.assertEqual(sample['measured_joints'][4]['angle'], 89)
    def test_explicit_targets_preserve_readings_and_backup(self):
        original = self.path.read_bytes()
        sample = pose_setup.capture(Fake(), self.cfg, self.path, 'ready', [90]*5, lambda _: 'y')
        self.assertEqual(json.loads(self.path.read_text())['poses']['ready'], [90]*5)
        self.assertEqual(sample['measured_joints'][4]['angle'], 89)
        self.assertEqual(Path(sample['backup']).read_bytes(), original)
    def test_cancel_and_simulation_no_write(self):
        original = self.path.read_bytes()
        pose_setup.capture(Fake(), self.cfg, self.path, 'ready', [90]*5, lambda _: 'n')
        self.assertEqual(self.path.read_bytes(), original)
        fake = Fake()
        fake.real = False
        with self.assertRaises(ValueError):
            pose_setup.capture(fake, self.cfg, self.path, 'ready', [90]*5)
    def test_external_edit_never_overwritten(self):
        original = self.path.read_bytes()
        self.path.write_text('user changes')
        with self.assertRaises(RuntimeError):
            pose_setup.save_config(self.path, self.cfg, original)
        self.assertEqual(self.path.read_text(), 'user changes')
    def test_invalid_and_missing_targets_rejected(self):
        for values in ([90]*4, [90,90,90,90,89], [90,90,90,90,271]):
            with self.assertRaises(ValueError):
                pose_setup.capture(Fake(), self.cfg, self.path, 'ready', values, lambda _: 'y')
        self.assertEqual(len(pose_setup.required(self.cfg)), 7)


if __name__ == '__main__':
    unittest.main()
