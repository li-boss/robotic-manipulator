"""合成测试夹具（原 test_auto 的 fixture）：仅数学计算与假设备，所有尺寸是测试数据，禁止部署。"""
import hashlib

from robot_model import MODEL, Robot, arm_fingerprint


def default_setup():
    return dict(version=1,model_reviewed=False,calibration_reviewed=False,path_reviewed=False,
        arm_config_sha256=None,
        camera=dict(matrix=None,distortion=None,image_size=[640,480],samples=[],anchor_frame=None),
        marks=dict(pixels=None,xy_mm=None),check_point=dict(pixel=None,xy_mm=None),height_checks=[],
        observation_angles=None,observation_readings=None,grasp_reference_angles=None,table_z_mm=None,block_size_mm=None,
        jaw_open_width_mm=None,max_position_error_mm=None,grasp_height_mm=None,
        transfer_height_mm=None,release_gap_mm=None,yellow_xy_mm=None,
        model=dict(urdf_sha256=hashlib.sha256(MODEL.read_bytes()).hexdigest(),servo_zero_deg=[90]*5,
                   servo_sign=[1]*5,tcp_xyz_mm=None,tcp_rpy_deg=None,collision_radii_mm=None,
                   tool_radius_mm=None,tool_boxes_mm=None,measurements=[]))


def fixture():
    cfg=default_setup()
    cfg.update(table_z_mm=0,block_size_mm=[30,30,30],jaw_open_width_mm=70,max_position_error_mm=1,
               grasp_height_mm=15,transfer_height_mm=100,release_gap_mm=5,yellow_xy_mm=[50,200],
               observation_angles=[90,50,32,82,90],observation_readings=[90,49,32,82,90],
               grasp_reference_angles=[90,30,30,30,90],
               model_reviewed=True,calibration_reviewed=True,path_reviewed=True)
    cfg['camera'].update(matrix=[[800,0,320],[0,800,240],[0,0,1]],distortion=[0]*5)
    cfg['marks']['xy_mm']=[[-100,100],[100,100],[100,300],[-100,300]]
    def project(xy,z=0):
        # 独立的已知相机：位于(0,200,500)，向下看。
        return [320+800*xy[0]/(500-z),240-800*(xy[1]-200)/(500-z)]
    cfg['marks']['pixels']=[project(xy) for xy in cfg['marks']['xy_mm']]
    cfg['check_point']=dict(xy_mm=[30,230],pixel=project([30,230]))
    cfg['height_checks']=[dict(xy_mm=xy,pixel=project(xy,30)) for xy in [[-50,150],[50,150],[0,260]]]
    cfg['model'].update(tcp_xyz_mm=[0,0,120],tcp_rpy_deg=[0,0,0],collision_radii_mm=[.1]*5,
                        tool_radius_mm=.1,tool_boxes_mm=[dict(center=[0,0,60],half_size=[.1,.1,.1])])
    arms=dict(hardware_ready=True,motion_ms=1500,settle_ms=300,
              joint_limits={str(i):[90,250] if i==5 else [10,170] for i in range(1,7)},
              gripper=dict(open=80,closed=129),poses={},targets={})
    robot=Robot(cfg,arms)
    cfg['model']['measurements']=[dict(angles=q,xyz_mm=(robot.fk(q)[0][:3,3]*1000).tolist())
                    for q in [[90,50,32,82,90],[95,55,32,82,90],[85,45,40,80,90]]]
    cfg['arm_config_sha256']=arm_fingerprint(arms)
    return cfg,arms

