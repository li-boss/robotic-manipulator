"""Manual/vision parity using synthetic pixels and local computation only."""
import unittest

import numpy as np

from modes import detection_to_target
from manipulation_geometry import build_plan, locate
from test_dynamic_manipulation import dynamic_case, outlined_target


class VisionGraspParityTests(unittest.TestCase):
    def targets(self, xy):
        q = [90, 80, 35, 10, 90, 90]
        cfg, arms, _, _ = dynamic_case(q)
        manual = outlined_target(cfg, q, xy)
        vision = detection_to_target(
            dict(corners=manual['outline_px'], center=manual['center_px'], score=.9),
            manual['image_size'], manual['frame_id'], 'pickup', None, 'block')
        for key in ('_frame_joint_angles', '_start_joint_angles'):
            vision[key] = manual[key].copy()
        return cfg, arms, manual, vision

    def test_same_pixels_produce_same_location_at_workspace_edges(self):
        for xy in ((0, 230), (-40, 230), (40, 230), (0, 260), (0, 300)):
            with self.subTest(xy=xy):
                cfg, _, manual, vision = self.targets(xy)
                self.assertEqual(locate(cfg, manual), locate(cfg, vision))
                np.testing.assert_allclose(locate(cfg, vision)['xyz_mm'][:2], xy, atol=.5)

    def test_same_pixels_produce_same_plan_or_same_rejection(self):
        for xy in ((0, 230), (-40, 230), (40, 230), (0, 260), (0, 300)):
            with self.subTest(xy=xy):
                cfg, arms, manual, vision = self.targets(xy)
                results = []
                for target in (manual, vision):
                    try:
                        plan = build_plan(cfg, arms, target, True)
                        results.append(('plan', plan['steps'], plan['poses']))
                    except ValueError as exc:
                        results.append(('rejected', str(exc)))
                self.assertEqual(results[0], results[1])

    def test_four_corners_and_legacy_box_use_different_projection_inputs(self):
        cfg, _, manual, vision = self.targets((0, 230))
        cfg['cad'].setdefault('camera_calibration', {})['projection_table'] = dict(
            kind='board_pose_estimate', z_mm=cfg['table_z_mm'] - 20)
        del manual['outline_px']
        a, b = locate(cfg, manual), locate(cfg, vision)
        self.assertEqual(a['projection_source'], 'calibrated')
        self.assertEqual(b['projection_source'], 'block_size_fit')
        self.assertGreater(np.linalg.norm(np.array(a['xyz_mm']) - b['xyz_mm']), 1)


if __name__ == '__main__':
    unittest.main()
