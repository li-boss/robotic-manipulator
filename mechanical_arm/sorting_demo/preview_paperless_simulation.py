"""本地无纸页面演示：仅合成图像和假控制器，禁止实机；资料放临时目录。"""
import argparse
import json
from pathlib import Path
import tempfile
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

from preview_manipulation_simulation import SyntheticCamera,OnlyFake
from server import Demo,demo_handler
from test_cad_manipulation import case
from manipulation_geometry import camera_pose


def application(root):
    cfg,arms,_,target=case()
    arm_path=root/'arm_playground/config.json';arm_path.parent.mkdir()
    arm_path.write_text(json.dumps(arms))
    config=root/'sorting_demo/config.json';config.parent.mkdir()
    config.write_bytes((Path(__file__).parent/'config.json').read_bytes())
    camera=SyntheticCamera();image=np.full((480,640,3),(44,37,29),np.uint8)
    k,d,r,t=camera_pose(cfg)
    world=np.array([[-15,245,14],[15,245,14],[15,275,14],[-15,275,14]],float)
    px=cv2.projectPoints(world,cv2.Rodrigues(r)[0],t,k,d)[0].reshape(-1,2)
    cv2.fillConvexPoly(image,np.rint(px).astype(np.int32),(40,210,245))
    cv2.putText(image,'SIMULATION ONLY - NO HARDWARE',(20,35),cv2.FONT_HERSHEY_SIMPLEX,.65,(230,230,230),1)
    camera.jpeg=cv2.imencode('.jpg',image)[1].tobytes()
    demo=Demo(camera,config,arm_path,root/'events.jsonl',real_control=False)
    OnlyFake.values=cfg['observation_angles']+[10];OnlyFake.calls=[]
    OnlyFake.fail_write=None;OnlyFake.fail_read=None;OnlyFake.reads=0
    demo.control.factory=OnlyFake;demo.manipulation.planner=None
    captured=demo.manipulation.capture({})
    cfg['cad']['anchor_frame']=captured['id'];cfg['cad']['assumptions_confirmed']=False
    demo.manipulation.path.write_text(json.dumps(cfg));demo.control.extra_audit_paths.append(demo.manipulation.path)
    # 不绕过证据、规划、执行或读数检查；模拟模式允许完整模拟，但永不计入实机验收。
    target['frame_id']=captured['id']
    demo.synthetic_target=target
    return demo


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8092)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='paperless_simulation_') as folder:
        demo=application(Path(folder));handler=demo_handler(demo)
        handler.log_message=lambda self,*args:None
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler)
        print('仅合成模拟：http://127.0.0.1:%d/manipulation'%args.port,flush=True)
        print('目标参考（合成数据）：'+json.dumps(demo.synthetic_target,ensure_ascii=False),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:demo.camera.stop.set();server.server_close()


if __name__=='__main__':main()
