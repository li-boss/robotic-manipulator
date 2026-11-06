"""人工旋转/透视标注与模型边长诊断；纯合成，无硬件。"""
import copy
import unittest
import cv2
import numpy as np
from manipulation_geometry import camera_pose,locate,build_plan
from test_dynamic_manipulation import dynamic_case
from test_manipulation import fixture


def project(cfg,points,q=None):
    k,d,r,t=camera_pose(cfg,q)
    return cv2.projectPoints(np.asarray(points,float),cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)


def rotated_target(cfg,q,yaw=45):
    theta=np.radians(yaw);rot=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
    corners=np.array([[-15,-15],[15,-15],[15,15],[-15,15]])@rot.T+[0,260]
    world=np.c_[corners,np.full(4,14.)]
    outline=project(cfg,world,q[:5]);center=project(cfg,[[0,260,14]],q[:5])[0]
    return dict(source='manual',task_mode='pickup',frame_id='a'*32,image_size=[640,480],block_size_id='block',
        bbox=[*outline.min(axis=0),*outline.max(axis=0)],outline_px=outline.tolist(),
        center_px=center.tolist(),edge_px=outline[:2].tolist(),_frame_joint_angles=q[:5],_start_joint_angles=q)


class RotatedAnnotationTests(unittest.TestCase):
    def test_rotated_and_perspective_top_face(self):
        cfg,_,_,_=dynamic_case();q=[100,78,37,10,110,90]
        for yaw in (0,17,45,75,120):
            t=rotated_target(cfg,q,yaw);out=locate(cfg,t)
            np.testing.assert_allclose(out['xyz_mm'],[0,260,14],atol=1e-4)
            self.assertAlmostEqual(out['edge_yaw_deg'],yaw)
            self.assertEqual(out['warnings'],[])
            t['outline_px']=list(reversed(t['outline_px']));self.assertEqual(out,locate(cfg,t))

    def test_cad_mismatch_warns_without_rescaling_center_or_size(self):
        cfg,arms,_,t=dynamic_case();q=t['_start_joint_angles']
        t['edge_px']=project(cfg,[[-12,245,14],[12,245,14]],q[:5]).tolist()
        before=copy.deepcopy(cfg);out=locate(cfg,t)
        self.assertAlmostEqual(out['edge_length_mm'],24.)
        self.assertEqual(out['expected_edge_length_mm'],30.)
        self.assertTrue(out['warnings']);self.assertEqual(cfg,before)
        np.testing.assert_allclose(out['xyz_mm'],[0,260,14],atol=1e-5)
        p=build_plan(cfg,arms,t,True)
        self.assertEqual(p['warnings'],out['warnings']);self.assertFalse(p['accuracy_verified'])
        self.assertEqual(cfg['block_size_mm'],[30,30,30])

    def test_45_degree_annotation_still_rejects_unsafe_motion(self):
        cfg,arms,_,_=dynamic_case();q=[90,80,35,10,90,90]
        target=rotated_target(cfg,q,45)
        self.assertAlmostEqual(locate(cfg,target)['edge_yaw_deg'],45.)
        before=copy.deepcopy(arms)
        with self.assertRaisesRegex(ValueError,'不可达|桌面'):
            build_plan(cfg,arms,target,True)
        self.assertEqual(arms,before)

    def test_crossed_duplicate_and_outside_top_face_rejected(self):
        cfg,_,_,_=dynamic_case();q=[90,80,35,10,90,90];t=rotated_target(cfg,q,45)
        for points in ([t['outline_px'][i] for i in (0,2,1,3)], [t['outline_px'][0]]*4):
            bad=copy.deepcopy(t);bad['outline_px']=points
            with self.assertRaises(ValueError):locate(cfg,bad)
        bad=copy.deepcopy(t);bad['center_px']=[bad['bbox'][0]+.1,bad['bbox'][1]+.1]
        with self.assertRaisesRegex(ValueError,'顶面内'):locate(cfg,bad)
        bad=copy.deepcopy(t);bad['edge_px'][1]=bad['edge_px'][0]
        with self.assertRaises(ValueError):locate(cfg,bad)

    def test_measured_calibration_keeps_metric_consistency_check(self):
        cfg,_,t=fixture()
        points=np.array(t['edge_px']);mid=points.mean(axis=0)
        t['edge_px']=(mid+(points-mid)*.5).tolist()
        with self.assertRaisesRegex(ValueError,'标定投影边长'):locate(cfg,t)


if __name__=='__main__':unittest.main()
