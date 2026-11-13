"""Equivalent square-block directions with synthetic camera/config only."""
import copy
import math
import unittest

import numpy as np

from test_cad_manipulation import case
from test_dynamic_manipulation import dynamic_case, outlined_target
from test_manipulation_guided import teaching_fixture
from manipulation_geometry import locate
from manipulation_guided import GuidedSearch
from manipulation_reference import build_reference, reference_seed
from manipulation_ik import candidates, wrist_seed


class DirectionSymmetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixed, _, _, _ = case()
        cls.cfg, cls.arms, cls.meta, _ = dynamic_case()
        cls.reference = build_reference(cls.cfg, cls.arms, teaching_fixture(fixed))
        cls.q = [row['angle'] for row in cls.meta['joints']]

    def compare_edges(self, distance):
        target = outlined_target(self.cfg, self.q, (0, distance))
        quad = np.asarray(target['outline_px'])
        # Same four physical points; left/top directions and their reversals.
        pairs = ((3, 0), (3, 2), (0, 3), (2, 3))
        snapshots = []
        for pair in pairs:
            value = copy.deepcopy(target)
            value['edge_px'] = quad[list(pair)].tolist()
            located = locate(self.cfg, value)
            search = GuidedSearch(self.cfg, self.arms, self.reference)
            search.located = located
            pickup = [*located['xyz_mm'][:2], self.cfg['table_z_mm'] + self.cfg['grasp_height_mm']]
            seed = reference_seed(self.reference, pickup[:2], 'grasp')['angles']
            union = {tuple(row['q']) for tilt in search.tilts(seed)
                     for row in search.options(pickup, located['edge_yaw_deg'], tilt, seed, seed, None)}
            passed, _ = search.grasp_endpoints(located)
            search.precheck_grasp(located)
            snapshots.append((located['xyz_mm'], union, {tuple(q) for q in passed}))
            for angles in union:
                self.assertTrue(all(type(v) is int for v in angles))
                self.assertTrue(all(self.arms['joint_limits'][str(i + 1)][0] <= v <=
                                    self.arms['joint_limits'][str(i + 1)][1] for i, v in enumerate(angles)))
        for snapshot in snapshots[1:]:
            self.assertEqual(snapshot, snapshots[0])

    def test_230mm_same_outline_changing_edge_keeps_legal_endpoint_candidates(self):
        self.compare_edges(230)

    def test_260mm_equivalent_directions_remain_identical(self):
        self.compare_edges(260)

    def test_near_quarter_turn_roundoff_does_not_drop_wrist_boundary_branch(self):
        target = outlined_target(self.cfg, self.q, (0, 230))
        located = locate(self.cfg, target)
        search = GuidedSearch(self.cfg, self.arms, self.reference)
        pickup = [*located['xyz_mm'][:2], self.cfg['table_z_mm'] + self.cfg['grasp_height_mm']]
        seed = reference_seed(self.reference, pickup[:2], 'grasp')['angles']
        expected = {tuple(row['q']) for row in candidates(search.robot, pickup, 0., 30., seed, seed)}
        self.assertTrue(any(q[4] == 90 for q in expected))
        for yaw in (-90.00000000000004, -90., -89.99999999999996, 90., 90.00000000000004, 180., 270.):
            with self.subTest(yaw=yaw):
                actual = {tuple(row['q']) for row in candidates(search.robot, pickup, yaw, 30., seed, seed)}
                self.assertEqual(actual, expected)

    def test_seed_cleanup_preserves_nonzero_directions(self):
        plane = -math.pi + math.radians(30)
        for relative in (1e-8, -1e-8, .17, -.42, math.pi / 4):
            with self.subTest(relative=relative):
                expected = math.atan2(math.sin(relative) / math.cos(plane), math.cos(relative))
                self.assertEqual(wrist_seed(relative, plane), expected)


if __name__ == '__main__':
    unittest.main()
