"""Synthetic edge-direction comparison using unchanged repository test fixtures.

Never imports/constructs an arm device, opens a network connection or executes a
plan. Checks only localization, candidate generation and grasp endpoint precheck.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def forbidden(*args, **kwargs):
    raise RuntimeError('Synthetic offline audit: network/hardware disabled')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    hardware = types.ModuleType('Arm_Lib')
    hardware.Arm_Device = forbidden
    sys.modules['Arm_Lib'] = hardware
    sys.path[:0] = [str(ROOT / 'board'), str(ROOT / 'board/tests')]
    import numpy as np
    from test_cad_manipulation import case
    from test_dynamic_manipulation import dynamic_case, outlined_target
    from test_manipulation_guided import teaching_fixture
    from manipulation_reference import build_reference, reference_seed
    from manipulation_geometry import locate, check_reach
    from manipulation_guided import GuidedSearch

    fixed, _, _, _ = case()
    teaching = teaching_fixture(fixed)
    cfg, arms, meta, _ = dynamic_case()
    q = [r['angle'] for r in meta['joints']]
    reference = build_reference(cfg, arms, teaching)
    write(out / 'synthetic_inputs.json', dict(scope='SYNTHETIC TEST ONLY, not edge_one or field calibration',
          fixture_sources=['test_cad_manipulation.case', 'test_dynamic_manipulation.dynamic_case',
                           'test_dynamic_manipulation.outlined_target', 'test_manipulation_guided.teaching_fixture'],
          setup=cfg, arms=arms, reference=reference, synthetic_start_angles=q))
    results = []
    for distance in (260, 230):
        target = outlined_target(cfg, q, (0, distance))
        quad = np.asarray(target['outline_px'])
        # These labels refer to image edges. Fixture outline order is unchanged.
        pairs = [(i, (i + 1) % 4) for i in range(4)]
        left = min(pairs, key=lambda pair: float(quad[list(pair), 0].mean()))
        top = min(pairs, key=lambda pair: float(quad[list(pair), 1].mean()))
        left = tuple(sorted(left, key=lambda i: quad[i, 1]))  # top to bottom
        top = tuple(sorted(top, key=lambda i: quad[i, 0]))    # left to right
        variants = dict(left=left, top=top, left_reversed=left[::-1], top_reversed=top[::-1])
        position = dict(scope='SYNTHETIC TEST ONLY', source_xy_mm=[0, distance],
                        unchanged_center_px=target['center_px'], unchanged_outline_px=target['outline_px'], variants={})
        all_sets, passing_sets = {}, {}
        for name, pair in variants.items():
            current = copy.deepcopy(target)
            current['edge_px'] = quad[list(pair)].tolist()
            assert {k: v for k, v in current.items() if k != 'edge_px'} == {k: v for k, v in target.items() if k != 'edge_px'}
            located = locate(cfg, current)
            check_reach(cfg, arms, located)
            search = GuidedSearch(cfg, arms, reference)
            search.located = located
            pickup = [*located['xyz_mm'][:2], cfg['table_z_mm'] + cfg['grasp_height_mm']]
            seed = reference_seed(reference, pickup[:2], 'grasp')['angles']
            margin = search.robot._cad_reach * math.radians(1.25)
            by_tilt, union, passed_union = [], set(), set()
            for tilt in search.tilts(seed):
                rows = []
                for option in search.options(pickup, located['edge_yaw_deg'], tilt, seed, seed, None):
                    angles = option['q']
                    union.add(tuple(angles))
                    pose = search.robot.fk(angles)[0]
                    z = float(pose[2, 3] * 1000)
                    reason = None
                    try:
                        search.robot.collision(angles, None, margin)
                    except ValueError as exc:
                        reason = str(exc)
                    if reason is None and not cfg['table_z_mm'] < z < cfg['table_z_mm'] + cfg['block_size_mm'][2]:
                        reason = '模型夹持中心不在方块高度内'
                    if reason is None:
                        passed_union.add(tuple(angles))
                    rows.append(dict(q=angles, position_error_mm=option['error_mm'], score=option['score'],
                                     tcp_z_mm=z, endpoint_pass=reason is None, rejected_reason=reason))
                by_tilt.append(dict(tilt_deg=tilt, candidate_count=len(rows), candidates=rows))
            try:
                search.precheck_grasp(located)
                precheck = dict(passed=True, error=None)
            except ValueError as exc:
                precheck = dict(passed=False, error=str(exc))
            assert precheck['passed'] == bool(passed_union)
            dx, dy = np.diff(np.asarray(current['edge_px']), axis=0)[0]
            position['variants'][name] = dict(target=current, located=located, pickup_xyz_mm=pickup,
                image_angle_deg=float(np.degrees(np.arctan2(dy, dx))),
                base_yaw_deg=located['edge_yaw_deg'],
                margin_mm=margin, unchanged_position_tolerance_mm=cfg['max_position_error_mm'],
                unchanged_direction_tolerance_deg=cfg['max_direction_error_deg'],
                grasp_height_check=dict(strictly_above_table_mm=cfg['table_z_mm'],
                                        strictly_below_block_top_mm=cfg['table_z_mm'] + cfg['block_size_mm'][2]),
                candidate_union=[list(v) for v in sorted(union)],
                passing_endpoint_union=[list(v) for v in sorted(passed_union)],
                by_tilt=by_tilt, precheck=precheck)
            all_sets[name], passing_sets[name] = union, passed_union
            print(json.dumps(dict(synthetic=True, distance_mm=distance, variant=name,
                  yaw_deg=located['edge_yaw_deg'], candidates=len(union), passing_endpoints=len(passed_union),
                  precheck=precheck), ensure_ascii=False), flush=True)
        baseline = position['variants']['left']
        for row in position['variants'].values():
            assert np.array_equal(row['located']['xyz_mm'], baseline['located']['xyz_mm'])
            assert row['located']['projection_offset_mm'] == baseline['located']['projection_offset_mm']
        position['comparisons_to_left'] = {
            name: dict(candidate_sets_equal=all_sets[name] == all_sets['left'],
                       passing_sets_equal=passing_sets[name] == passing_sets['left'],
                       candidates_only_in_left=[list(v) for v in sorted(all_sets['left'] - all_sets[name])],
                       candidates_only_in_variant=[list(v) for v in sorted(all_sets[name] - all_sets['left'])],
                       passing_only_in_left=[list(v) for v in sorted(passing_sets['left'] - passing_sets[name])],
                       passing_only_in_variant=[list(v) for v in sorted(passing_sets[name] - passing_sets['left'])],
                       precheck_equal=position['variants'][name]['precheck']['passed'] == baseline['precheck']['passed'])
            for name in variants}
        results.append(position)
        write(out / ('synthetic_' + str(distance) + 'mm.json'), position)
    summary = dict(scope='SYNTHETIC TEST ONLY', no_hardware=True, no_network=True, production_code_unchanged=True,
                   fixture_parameters_unchanged=True, results=[dict(distance_mm=p['source_xy_mm'][1],
                       variants={k: dict(yaw_deg=v['base_yaw_deg'], candidates=len(v['candidate_union']),
                           passing=len(v['passing_endpoint_union']), precheck=v['precheck']) for k, v in p['variants'].items()},
                       comparisons=p['comparisons_to_left']) for p in results])
    write(out / 'summary.json', summary)
    sources = ['board/manipulation_ik.py', 'board/manipulation_guided.py', 'board/manipulation_geometry.py',
               'board/tests/test_cad_manipulation.py', 'board/tests/test_dynamic_manipulation.py',
               'board/tests/test_manipulation_guided.py', 'tools/offline_direction_audit.py']
    write(out / 'code_provenance.json', {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources})


if __name__ == '__main__':
    main()
