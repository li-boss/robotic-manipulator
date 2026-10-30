"""仅本地浏览器QA：合成相机和假控制器，无实机选项；合成配置只存临时目录。"""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

import manipulation_task
from manipulation_geometry import build_plan as checked_plan,readiness
from server import Demo,demo_handler
from test_manipulation import fixture,StatefulFake


class SyntheticCamera:
    def __init__(self):
        self.condition=threading.Condition();self.stop=threading.Event();self.sequence=1
        self.device='SYNTHETIC ONLY';self.size=[640,480]
        image=np.full((480,640,3),(44,37,29),np.uint8)
        for x in range(0,640,40):cv2.line(image,(x,0),(x,479),(70,62,50),1)
        for y in range(0,480,40):cv2.line(image,(0,y),(639,y),(70,62,50),1)
        cv2.rectangle(image,(299,295),(341,337),(40,210,245),-1)
        cv2.putText(image,'SIMULATION - NO HARDWARE',(20,35),cv2.FONT_HERSHEY_SIMPLEX,.7,(210,225,240),1)
        self.jpeg=cv2.imencode('.jpg',image)[1].tobytes();self.updated=time.monotonic()

    def status(self):
        with self.condition:
            self.updated=time.monotonic()
            return dict(connected=True,device=self.device,resolution=self.size,message='合成静态画面')


class OnlyFake(StatefulFake):
    def __init__(self,config,real=False):
        if real:raise AssertionError('测试服务禁止实机')
        super().__init__(config,real=False)


def application(root):
    cfg,arms,_=fixture();cfg['transfer_height_mm']=50;cfg['destinations']['test']=[70,127.7]
    arm_path=root/'arm_playground/config.json';arm_path.parent.mkdir()
    arm_path.write_text(json.dumps(arms));config=root/'sorting_demo/config.json';config.parent.mkdir()
    config.write_bytes((Path(__file__).parent/'config.json').read_bytes())
    camera=SyntheticCamera();demo=Demo(camera,config,arm_path,root/'events.jsonl',real_control=False)
    OnlyFake.values=cfg['observation_angles']+[arms['gripper']['open']];OnlyFake.calls=[]
    OnlyFake.fail_write=None;OnlyFake.fail_read=None;OnlyFake.reads=0
    demo.control.factory=OnlyFake;demo.manipulation.planner=None
    demo.manipulation.path.write_text(json.dumps(cfg));demo.control.extra_audit_paths.append(demo.manipulation.path)
    # 合成测试明确跳过实物证据，但仍使用真实定位、IK、连续路径和控制层校验。
    # 这些替换仅存在本地QA进程，禁止将本脚本作为设备服务部署。
    demo.manipulation.check_evidence=lambda cfg:None
    demo.manipulation.check_path_evidence=lambda cfg:None
    manipulation_task.build_plan=lambda cfg,arms,target,require_path=True:checked_plan(cfg,arms,target,False)
    original_status=demo.manipulation.status
    def status():
        value=original_status();value['execution_readiness']=readiness(demo.manipulation.load(),demo.control.config(),False)
        value['message']='SYNTHETIC ONLY；没有现场验证，不可计为实机证据'
        return value
    demo.manipulation.status=status
    return demo


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8091)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='manipulation_simulation_') as folder:
        demo=application(Path(folder));server=ThreadingHTTPServer(('127.0.0.1',args.port),demo_handler(demo))
        print(f'仅合成模拟，未连接相机或机械臂：http://127.0.0.1:{args.port}/manipulation',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:demo.camera.stop.set();server.server_close()


if __name__=='__main__':main()
