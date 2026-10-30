"""仅127.0.0.1:8092且相机标记为SYNTHETIC ONLY的模拟端到端；不访问开发板。"""
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'sorting_demo'))
from test_cad_manipulation import target_at

BASE='http://127.0.0.1:8092'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=30) as response:return json.load(response)


def browser_numbers(value):
    if isinstance(value,dict):return {k:browser_numbers(v) for k,v in value.items()}
    if isinstance(value,list):return [browser_numbers(v) for v in value]
    return int(value) if type(value) is float and value.is_integer() else value


def main():
    state=get('/manipulation/status');control=get('/control/status');camera=get('/status')
    if state['mode']!='simulation' or control['mode']!='simulation' or camera.get('device')!='SYNTHETIC ONLY':
        raise ValueError('非专用合成模拟服务，拒绝任何提交')
    def post(path,body):
        req=urllib.request.Request(BASE+path,data=json.dumps(dict(body,csrf=control['csrf'])).encode(),
            headers={'Content-Type':'application/json','Origin':BASE})
        with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
    cfg=browser_numbers(state['setup']);cfg['cad']['assumptions_confirmed']=True
    saved=post('/manipulation/settings',dict(setup=cfg,expected_sha256=state['setup_sha256']))
    if not saved['setup']['cad']['assumptions_confirmed']:raise ValueError('浏览器数值规范化后候选采用状态丢失')
    if saved['setup']['model_reviewed'] or saved['setup']['calibration_reviewed']:raise ValueError('模拟冒充标定')
    frozen=post('/manipulation/capture',{})
    target=target_at(saved['setup'],[0,260],0);target['frame_id']=frozen['id']
    located=post('/manipulation/target',dict(target=target))
    preview=post('/manipulation/preview',dict(target=target,scene_confirmed=True,dry_run=False))
    if preview['mode']!='simulation':raise ValueError('预览模式错误，未执行')
    token=preview['token'];post('/control/execute',dict(token=token,confirmed=True))
    for _ in range(150):
        job=get('/control/status')['job']
        if job['status']!='running':break
        time.sleep(.2)
    if job['status']!='completed':raise RuntimeError('模拟失败：'+json.dumps(job))
    duplicate=post('/control/execute',dict(token=token,confirmed=True))['job']
    if duplicate.get('sent')!=job.get('sent'):raise ValueError('去重状态错误')
    result=post('/manipulation/result',dict(task_id=job['id'],result='success',note='纯合成无纸HTTP验证，不是现场结果'))
    if any(v['trials'] for v in result['acceptance'].values()):raise ValueError('模拟计入实机统计')
    report=dict(mode='simulation',task_mode='pickup',location=located['target'],
        steps=len(preview['parameters']['plan']['steps']),sent=job['sent'],verified_steps=job['verified_steps'],
        geometry_source=preview['parameters']['plan']['geometry_source'],
        approach_tilt_deg=preview['parameters']['plan']['approach_tilt_deg'],
        duplicate_did_not_resend=True,browser_number_roundtrip=True,real_trials=0,hardware_connected=False)
    (ROOT/'implementation_backups/paperless_20261016/http_simulation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
