"""纯计算验证夹持即停策略；不连接设备。"""
import unittest
import test_teaching as fixtures
from teaching_plan import fit,build,interpolate,prerequisites

class PickupTests(unittest.TestCase):
    def setup_mapping(self):
        cfg,arms=fixtures.fixture()
        cfg.update(task_mode='pickup_only',common={},orders={})
        for sample in cfg['samples']:sample['stage_orders']={'above':[1,5,4,3,2,6],'grasp':[1,5,4,3,2,6]}
        cfg['fit']=fit(cfg,arms)
        for v in cfg['validations']:v['fit_version']=cfg['fit']['version']
        return cfg,arms

    def test_no_common_poses_and_exact_card_order_final_grip(self):
        cfg,arms=self.setup_mapping();prerequisites(cfg,arms)
        plan=build(cfg,arms,[200,160])
        self.assertEqual([s['joints'][0][0] for s in plan['steps']],[1,5,4,3,2,6]*2)
        self.assertEqual(plan['steps'][-1]['joints'],[[6,130]])
        self.assertEqual(plan['task_mode'],'pickup_only')
        self.assertTrue(all(s['label'] in ['取物上方','抓取并保持'] for s in plan['steps']))

    def test_mixed_orders_and_closing_before_descent_rejected(self):
        cfg,arms=self.setup_mapping()
        for sample in cfg['samples']:sample['stage_orders']['grasp']=[6,1,5,4,3,2]
        cfg['fit']=fit(cfg,arms)
        with self.assertRaises(ValueError):build(cfg,arms,[200,160],require_validated=False)
        cfg,arms=self.setup_mapping()
        cfg['samples'][0]['stage_orders']['above']=[2,1,5,4,3,6];cfg['fit']=fit(cfg,arms)
        with self.assertRaises(ValueError):interpolate(cfg,arms,[200,160],False)

if __name__=='__main__':unittest.main()
