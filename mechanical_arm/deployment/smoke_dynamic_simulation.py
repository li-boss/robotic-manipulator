"""仅本机随机端口的完整假HTTP抓取；固定假相机/OnlyFake，禁止实机。"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'sorting_demo'))
from preview_paperless_simulation import application
from preview_manipulation_simulation import OnlyFake
from server import demo_handler
from test_dynamic_manipulation import marked_target


def main():
    with tempfile.TemporaryDirectory(prefix='dynamic_http_fake_') as folder:
        demo=application(Path(folder))
        assert demo.control.real is False and demo.control.factory is OnlyFake
        handler=demo_handler(demo);handler.log_message=lambda self,*args:None
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        base='http://127.0.0.1:'+str(server.server_port)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def get(path):
            with urllib.request.urlopen(base+path,timeout=15) as r:return json.load(r)
        try:
            assert get('/status')['device']=='SYNTHETIC ONLY'
            assert get('/control/status')['mode']=='simulation'
            csrf=get('/control/status')['csrf']
            def post(path,data):
                req=urllib.request.Request(base+path,data=json.dumps(dict(data,csrf=csrf)).encode(),
                    headers={'Content-Type':'application/json','Origin':base})
                with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
            anchor=post('/manipulation/capture',{})
            s=get('/manipulation/status')
            s=post('/manipulation/prepare-cad',dict(frame_id=anchor['id'],capture_mode='joint_snapshot_v1',
                intrinsics_recipe={'kind':'fov_estimate','axis':'diagonal','fov_deg':110},expected_sha256=s['setup_sha256']))
            assert s['execution_readiness']['ready'] and not s['setup']['cad']['assumptions_confirmed']
            q=[100,78,37,10,110,90];OnlyFake.values=q.copy()
            frozen=post('/manipulation/capture',{})
            target=marked_target(s['setup'],q);target['frame_id']=frozen['id']
            target.pop('_start_joint_angles');target.pop('_frame_joint_angles')
            located=post('/manipulation/target',{'target':target})
            p=post('/manipulation/preview',{'target':target})
            assert p['parameters']['plan']['initial_angles']==q and not OnlyFake.calls
            post('/control/execute',{'token':p['token'],'confirmed':True})
            for _ in range(300):
                job=get('/control/status')['job']
                if job['status']!='running':break
                time.sleep(.05)
            assert job['status']=='completed',job.get('error')
            count=len(OnlyFake.calls)
            post('/control/execute',{'token':p['token'],'confirmed':True})
            assert len(OnlyFake.calls)==count
            result=post('/manipulation/result',{'task_id':job['id'],'result':'success','note':'LOCAL SYNTHETIC ONLY'})
            assert not any(v['trials'] for v in result['acceptance'].values())
            out=dict(mode='simulation',hardware_connected=False,initial_angles=q,
                xyz_mm=located['target']['xyz_mm'],commands=count,verified=job['verified_steps'],
                final_confirmation_only=True,prior_empty_path_required=False,duplicate_not_resent=True,real_trials=0)
            path=ROOT/'implementation_backups/dynamic_grasp_20261023/http_simulation_report.json'
            path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(out,ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();demo.camera.stop.set();worker.join()


if __name__=='__main__':main()
