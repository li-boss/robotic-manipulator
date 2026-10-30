"""任意静止姿态抓取：只用假相机和假控制器，不访问硬件。"""
import copy
import json
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import cad_geometry as cad
from manipulation_geometry import camera_pose,locate,build_plan,readiness,observation,project_to_plane
from test_cad_manipulation import case
import test_cad_manipulation as cad_tests
from test_manipulation import StatefulFake


def dynamic_case(q=None):
    cfg,arms,meta,_=case()
    q=q or [90,80,35,10,90,90]
    meta['joints']=[dict(id=i,angle=v,read_ok=True,error=None) for i,v in enumerate(q,1)]
    cfg=cad.prepare(cfg,arms,meta,None,cfg['cad']['intrinsics_recipe'],capture_mode='joint_snapshot_v1')
    return cfg,arms,meta,marked_target(cfg,q)


def marked_target(cfg,q,xy=(0,260)):
    world=np.array([[*xy,14],[xy[0]-15,xy[1]-15,14],[xy[0]+15,xy[1]-15,14]],float)
    k,d,r,t=camera_pose(cfg,q[:5]);px=cv2.projectPoints(world,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
    return dict(source='manual',task_mode='pickup',frame_id='a'*32,image_size=[640,480],block_size_id='block',
        bbox=[*(px.min(axis=0)-10),*(px.max(axis=0)+10)],center_px=px[0].tolist(),edge_px=px[1:].tolist(),
        _frame_joint_angles=q[:5],_start_joint_angles=q.copy())


class DynamicGeometryTests(unittest.TestCase):
    def test_no_observation_target_or_prior_path_required(self):
        cfg,arms,meta,target=dynamic_case()
        self.assertIsNone(cfg['observation_angles']);self.assertIsNone(cfg['grasp_reference_angles'])
        self.assertFalse(cfg['cad']['assumptions_confirmed'])
        self.assertTrue(readiness(cfg,arms,True)['ready'])
        self.assertFalse(cfg['model_reviewed']);self.assertFalse(cfg['calibration_reviewed'])
        self.assertEqual(cfg['path_validations'],[])
        observation(cfg,arms,meta['joints'])  # 6号为90，而计划明确张开到10。

    def test_prepare_preserves_existing_fixed_destinations(self):
        cfg,arms,meta,_=dynamic_case();cfg['destinations']={'saved':[50,260]}
        updated=cad.prepare(cfg,arms,meta,None,cfg['cad']['intrinsics_recipe'],capture_mode='joint_snapshot_v1')
        self.assertEqual(updated['destinations'],{'saved':[50,260]})

    def test_projection_uses_each_frame_not_setup_anchor(self):
        cfg,arms,_,_=dynamic_case()
        for q in ([90,80,35,10,90,170],[100,78,37,10,110,90],[85,80,35,15,89,90]):
            with self.subTest(q=q):
                target=marked_target(cfg,q)
                np.testing.assert_allclose(locate(cfg,target)['xyz_mm'],[0,260,14],atol=1e-5)
                observation(cfg,arms,[dict(id=i,angle=v,read_ok=True) for i,v in enumerate(q,1)])

    def test_dynamic_start_and_opening_are_in_same_confirmed_plan(self):
        q=[100,78,37,10,110,90];cfg,arms,_,target=dynamic_case(q)
        p=build_plan(cfg,arms,target,True)
        self.assertEqual(p['initial_angles'],q)
        # 张开夹爪与第一段移动同批下发，夹爪在批内最后发出。
        self.assertEqual(p['steps'][0]['label'],'张开夹爪并到取物上方')
        self.assertEqual(p['steps'][0]['joints'][-1],[6,10])
        self.assertEqual(p['capture_mode'],'joint_snapshot_v1')
        self.assertEqual(p['steps'][-1]['label'],'抬升方块')
        self.assertNotEqual(p['poses'][0],build_plan(cfg,arms,marked_target(cfg,[90,80,35,10,90,90]),True)['poses'][0])
        bad=copy.deepcopy(target);bad['_start_joint_angles'][0]+=1
        with self.assertRaisesRegex(ValueError,'起始姿态'):build_plan(cfg,arms,bad,True)

    def test_boundary_reading_is_not_turned_into_outgoing_target(self):
        cfg,arms,_,target=dynamic_case([90,80,35,10,89,90])
        p=build_plan(cfg,arms,target,True)
        self.assertEqual(p['initial_angles'][4],89)
        for step in p['steps']:
            for i,v in step['joints']:
                self.assertEqual(type(v),int)
                self.assertGreaterEqual(v,arms['joint_limits'][str(i)][0])
        self.assertFalse(any(i==5 and v==89 for s in p['steps'] for i,v in s['joints']))

    def test_full_place_retracts_without_returning_to_raw_readings(self):
        cfg,arms,_,target=dynamic_case()
        cfg['destinations']['drop']=[-260*np.sin(np.radians(15)),260*np.cos(np.radians(15))]
        target.update(task_mode='pick_place',destination={'kind':'fixed','name':'drop'})
        p=build_plan(cfg,arms,target,True)
        self.assertEqual(p['steps'][-1]['label'],'撤离放置点')
        self.assertTrue(any(s['label']=='释放方块' for s in p['steps']))
        # 每个阶段同步一次下发；批内关节不重复，撤离不转腕。
        labels=[s['label'] for s in p['steps']]
        self.assertEqual(len(labels),len(set(labels)),labels)
        self.assertTrue(any(len(s['joints'])>1 for s in p['steps']))
        for s in p['steps']:self.assertEqual(len({i for i,_ in s['joints']}),len(s['joints']))
        self.assertFalse(any(i==5 for i,_ in p['steps'][-1]['joints']))
        # 运输段衔接：只在相邻移动段之间，夹持/释放前后及最后一步保持停稳。
        blended=[s['label'] for s in p['steps'] if s.get('blend_next_ms')]
        self.assertTrue(blended,labels)
        for i,step in enumerate(p['steps']):
            if step.get('blend_next_ms'):
                self.assertNotIn(p['steps'][i+1]['label'],('夹持方块','释放方块'))
                self.assertNotIn(step['label'],('夹持方块','释放方块','接近抓取','下降放置'))
        self.assertIsNone(p['steps'][-1].get('blend_next_ms'))


def outlined_target(cfg,q,xy=(0,260),half=15):
    z=cfg['table_z_mm']+30
    corners=np.array([[xy[0]-half,xy[1]-half,z],[xy[0]+half,xy[1]-half,z],[xy[0]+half,xy[1]+half,z],[xy[0]-half,xy[1]+half,z]],float)
    k,d,r,t=camera_pose(cfg,q[:5]);px=cv2.projectPoints(corners,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
    center=cv2.projectPoints(np.array([[*xy,z]],float),cv2.Rodrigues(r)[0],t,k,d)[0].reshape(2)
    target=marked_target(cfg,q,xy)
    target.update(bbox=[*(px.min(axis=0)-10),*(px.max(axis=0)+10)],outline_px=px.tolist(),center_px=center.tolist(),edge_px=px[:2].tolist())
    return target


class SelfScaleTests(unittest.TestCase):
    def test_block_size_fit_recovers_position_under_wrong_projection_plane(self):
        q=[90,80,35,10,90,90];cfg,_,_,_=dynamic_case(q);target=outlined_target(cfg,q)
        cfg['cad'].setdefault('camera_calibration',{})['projection_table']=dict(kind='board_pose_estimate',z_mm=cfg['table_z_mm']-20)
        wrong=project_to_plane(cfg,target['center_px'],cfg['table_z_mm']+30,q[:5])
        self.assertGreater(np.linalg.norm(wrong[:2]-[0,260]),3)
        located=locate(cfg,target)
        self.assertEqual(located['projection_source'],'block_size_fit')
        np.testing.assert_allclose(located['xyz_mm'][:2],[0,260],atol=.5)
        self.assertAlmostEqual(located['projection_offset_mm'],0,delta=.5)
        self.assertEqual(located['xyz_mm'][2],cfg['table_z_mm']+30)

    def test_inconsistent_outline_falls_back_with_warning(self):
        q=[90,80,35,10,90,90];cfg,_,_,_=dynamic_case(q);target=outlined_target(cfg,q)
        outline=np.array(target['outline_px']);outline[2]+=(outline[2]-outline[0])*.6
        target['outline_px']=outline.tolist();target['bbox']=[*(outline.min(axis=0)-10),*(outline.max(axis=0)+10)]
        located=locate(cfg,target)
        self.assertEqual(located['projection_source'],'calibrated')
        self.assertTrue(any('自校未采用' in w for w in located['warnings']))

    def test_out_of_reach_target_reports_distance_before_search(self):
        from manipulation_geometry import reach_range
        q=[90,80,35,10,90,90];cfg,arms,_,_=dynamic_case(q)
        low,high=reach_range(cfg,arms,cfg['table_z_mm']+cfg['grasp_height_mm'])
        self.assertLess(low,high)
        far=marked_target(cfg,q,(0,high+40))
        with self.assertRaisesRegex(ValueError,'超出机械臂在夹取高度的伸展范围'):build_plan(cfg,arms,far,True)


class DynamicTaskTests(cad_tests.CadTaskTests):
    def setUp(self):
        super().setUp()
        # 模型安装在基准姿态采样，真正任务会从另一姿态拍照。
        self.task.prepare_cad(dict(frame_id=self.meta['id'],capture_mode='joint_snapshot_v1',
            intrinsics_recipe=self.task.load()['cad']['intrinsics_recipe'],expected_sha256=self.task.sha()))

    # 只覆盖本文件针对动态分支的新测试，避免继承固定观察测试的断言。
    test_setup_preview_execute_and_dedup_without_paper_or_detection=None
    test_cannot_mark_cad_as_measured_or_record_simulated_path=None
    test_edited_source_invalidates_pending_plan_before_any_write=None

    def fresh_target(self,q=None):
        q=q or [100,78,37,10,110,90]
        StatefulFake.values=q.copy();meta=self.task.capture({})
        t=marked_target(self.task.load(),q);t['frame_id']=meta['id']
        return t,meta

    def test_changed_pose_closed_empty_claw_preview_execute_dedupe(self):
        target,meta=self.fresh_target();target['_start_joint_angles']=[0]*6
        with patch('auto_task.detect',side_effect=AssertionError('no vision')):
            p=self.task.preview({'target':target,'dry_run':False})
        self.assertEqual(p['parameters']['plan']['initial_angles'],[r['angle'] for r in meta['joints']])
        self.assertIn('夹爪空载',p['conditions']);self.assertIn('成像参数',p['conditions'])
        self.assertFalse(p['parameters']['dry_run']);self.assertEqual(StatefulFake.calls,[])
        with patch.object(self.task,'capture',side_effect=AssertionError('no new camera')):
            self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        n=len(StatefulFake.calls);self.assertGreater(n,0)
        self.c.execute({'token':p['token'],'confirmed':True});self.assertEqual(n,len(StatefulFake.calls))
        self.assertEqual(self.task.load()['path_validations'],[])
        self.assertTrue(all(x['trials']==0 for x in self.task.acceptance().values()))

    def test_estimated_edge_warning_is_in_final_confirmation(self):
        target,_=self.fresh_target();cfg=self.task.load();q=target['_start_joint_angles']
        k,d,r,t=camera_pose(cfg,q[:5])
        world=np.array([[-12,245,14],[12,245,14]],float)
        target['edge_px']=cv2.projectPoints(world,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2).tolist()
        p=self.task.preview({'target':target,'dry_run':False})
        warnings=p['parameters']['plan']['warnings']
        self.assertTrue(warnings)
        for warning in warnings:self.assertIn(warning,p['conditions'])
        self.assertEqual(StatefulFake.calls,[])

    def test_moved_after_freeze_rejected_and_private_values_overwritten(self):
        target,meta=self.fresh_target();target['_frame_joint_angles']=[0]*5
        target['_start_joint_angles']=[0]*6
        result,_=self.task.target(self.task.load(),target)
        np.testing.assert_allclose(result['xyz_mm'],[0,260,14],atol=1e-5)
        StatefulFake.values[0]+=3
        with self.assertRaisesRegex(ValueError,'离开冻结'):self.task.preview({'target':target})
        self.assertEqual(StatefulFake.calls,[])

    def test_optional_step_uses_actual_closed_claw_start(self):
        target,_=self.fresh_target()
        draft=self.task.preview({'target':target,'dry_run':True})
        p=self.task.step({'draft_id':draft['draft_id'],'index':0,'empty_confirmed':True})
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        self.assertEqual(StatefulFake.calls,[[tuple(x) for x in draft['plan']['steps'][0]['joints']]])
        self.assertEqual(StatefulFake.calls[0][-1],(6,10))
        self.assertEqual(self.task.drafts[draft['draft_id']]['next_step'],1)

    def test_read_failure_stops_without_followup_writes(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.fail_read=StatefulFake.reads+1
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'failed');self.assertEqual(StatefulFake.calls,[])


    def test_post_write_read_failure_has_no_followup_motion(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.fail_read=StatefulFake.reads+2
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        first=[tuple(pair) for pair in p['parameters']['plan']['steps'][0]['joints']]
        self.assertEqual(self.c.job['status'],'failed');self.assertEqual(StatefulFake.calls,[first])

    def test_partial_read_failures_warn_and_continue_whole_plan(self):
        target,_=self.fresh_target()
        StatefulFake.failed_joints={StatefulFake.reads+1:(2,3),StatefulFake.reads+2:(2,3)}
        p=self.task.preview({'target':target})
        self.assertIn('读取失败',[x['angle'] if x['read_ok'] else '读取失败' for x in p['joints']])
        start=StatefulFake.reads
        # 执行前读取与第一步后读取均有关节失败，只记录警告。
        StatefulFake.failed_joints={start+1:(2,3),start+2:(4,)}
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        steps=p['parameters']['plan']['steps']
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        self.assertEqual(len(StatefulFake.calls),len(steps))
        stopped=[x for x in steps if not x.get('blend_next_ms')]
        self.assertEqual(self.c.job['verified_steps'],len(stopped)-1)
        self.assertIn('读数/写入警告',self.c.job['message'])
        log=[json.loads(x) for x in self.c.log.read_text().splitlines()]
        self.assertTrue(any(e['kind']=='read_warning' for e in log))

    def test_valid_reading_mismatch_still_stops_after_partial_failure(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.failed_joints={StatefulFake.reads+1:(5,)}
        StatefulFake.values[1]+=2
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'failed');self.assertEqual(StatefulFake.calls,[])

    def test_gripper_blocked_by_block_warns_and_continues(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.blocked_gripper=110
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        result=self.c.job
        self.assertTrue(any(w['kind']=='gripper_blocked' for w in result['read_warnings']))

    def test_execution_reuses_previewed_plan_without_recomputing(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        with patch('manipulation_task.build_plan',side_effect=AssertionError('no recompute')),\
             patch.object(self.task,'compute_plan',side_effect=AssertionError('no recompute')):
            self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        self.assertEqual(len(StatefulFake.calls),len(p['parameters']['plan']['steps']))

    def test_tampered_plan_is_rejected_before_any_write(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        self.c.pending['parameters']['plan']['steps'][0]['joints'][0][1]+=1
        with self.assertRaisesRegex(ValueError,'计划不一致'):self.c.execute({'token':p['token'],'confirmed':True})
        self.assertEqual(StatefulFake.calls,[])

    def test_post_step_deviation_up_to_three_degrees_warns(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.shortfall={2:2}
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        self.assertTrue(any(w['kind']=='deviation' and w['joint']==2 for w in self.c.job['read_warnings']))
        self.assertLess(self.c.job['verified_steps'],len(p['parameters']['plan']['steps']))

    def test_post_step_deviation_over_three_degrees_stops(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.shortfall={2:4}
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'failed');self.assertIn('超过3度',self.c.job['error'])
        self.assertLess(len(StatefulFake.calls),len(p['parameters']['plan']['steps']))

    def test_recovered_write_retry_is_logged_and_plan_continues(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.simulated_retries={2:[dict(joint=2,angle=53,attempt=1,error='Arm_serial_servo_write I2C error')]}
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        self.assertEqual(StatefulFake.retries_requested,2)
        self.assertEqual(len(StatefulFake.calls),len(p['parameters']['plan']['steps']))
        self.assertTrue(any(w['kind']=='write_retry' and w['step']==1 for w in self.c.job['read_warnings']))
        log=[json.loads(x) for x in self.c.log.read_text().splitlines()]
        self.assertTrue(any(e['kind']=='write_retry' and e['joint']==2 for e in log))

    def test_capture_returns_reach_overlay(self):
        StatefulFake.values=[90,80,35,10,90,90];meta=self.task.capture({})
        overlay=meta['reach_overlay']
        self.assertIsNotNone(overlay);self.assertEqual(len(overlay['radii_mm']),2)
        for line in overlay['lines']:self.assertGreater(len(line['points']),1)
        self.assertEqual(StatefulFake.calls,[])

    def test_blended_steps_send_early_without_readback(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        steps=p['parameters']['plan']['steps'];reads=StatefulFake.reads
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'completed',self.c.job)
        expected=[s['motion_ms']-s['blend_next_ms'] if s.get('blend_next_ms') else None for s in steps]
        self.assertEqual(StatefulFake.holds,expected)
        # 执行前1次 + 每个停稳步骤1次。
        self.assertEqual(StatefulFake.reads-reads,1+sum(1 for s in steps if not s.get('blend_next_ms')))

    def test_projection_plane_only_changes_image_conversion(self):
        from manipulation_geometry import project_to_plane
        cfg,_,_,target=dynamic_case();q=target['_frame_joint_angles']
        base=project_to_plane(cfg,target['center_px'],cfg['table_z_mm']+30-40,q)
        cfg['cad'].setdefault('camera_calibration',{})['projection_table']=dict(kind='board_pose_estimate',z_mm=cfg['table_z_mm']-40)
        moved=project_to_plane(cfg,target['center_px'],cfg['table_z_mm']+30,q)
        np.testing.assert_allclose(moved[:2],base[:2]);self.assertEqual(moved[2],cfg['table_z_mm']+30)

    def test_empty_step_validation_stays_strict_on_partial_failure(self):
        target,_=self.fresh_target()
        draft=self.task.preview({'target':target,'dry_run':True})
        p=self.task.step({'draft_id':draft['draft_id'],'index':0,'empty_confirmed':True})
        StatefulFake.failed_joints={StatefulFake.reads+1:(2,)}
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'failed');self.assertEqual(StatefulFake.calls,[])

    def test_pose_change_after_preview_has_zero_writes(self):
        target,_=self.fresh_target();p=self.task.preview({'target':target})
        StatefulFake.values[1]+=2
        self.c.execute({'token':p['token'],'confirmed':True});self.wait_done()
        self.assertEqual(self.c.job['status'],'failed');self.assertEqual(StatefulFake.calls,[])


if __name__=='__main__':unittest.main()
