"""仅调用127.0.0.1纯模拟测试服务；双重核对模式，禁止访问设备实机服务。"""
import json
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8091'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=60) as response:return json.load(response)


def main():
    control=get('/control/status');state=get('/manipulation/status')
    if control['mode']!='simulation' or state['mode']!='simulation' or state['setup']['scene_revision']!='SYNTHETIC TEST ONLY':
        raise ValueError('非专用纯模拟服务，禁止执行')
    def post(path,body):
        request=urllib.request.Request(BASE+path,data=json.dumps(dict(body,csrf=control['csrf'])).encode(),
            headers={'Content-Type':'application/json','Origin':BASE},method='POST')
        with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
    frozen=post('/manipulation/capture',{})
    v=240-800*(145.65-200)/570;half=800*15/570
    target=dict(source='manual',frame_id=frozen['id'],image_size=[640,480],block_size_id='block',
        bbox=[296,v-24,344,v+24],center_px=[320,v],edge_px=[[320-half,v+half],[320+half,v+half]],
        destination={'kind':'fixed','name':'test'})
    preview=post('/manipulation/preview',dict(target=target,scene_confirmed=True))
    if preview['mode']!='simulation':raise ValueError('预览模式异常，未执行')
    token=preview['token'];accepted=post('/control/execute',dict(token=token,confirmed=True))
    for _ in range(150):
        job=get('/control/status')['job']
        if job['status']!='running':break
        time.sleep(.2)
    if job['status']!='completed':raise RuntimeError('模拟任务失败：'+json.dumps(job))
    duplicate=post('/control/execute',dict(token=token,confirmed=True))['job']
    if duplicate['id']!=job['id'] or duplicate.get('sent')!=job.get('sent'):raise ValueError('去重状态异常')
    result=post('/manipulation/result',dict(task_id=job['id'],result='success',note='纯模拟端到端测试，不是实机结果'))
    if any(v['trials'] for v in result['acceptance'].values()):raise ValueError('模拟结果错误计入实机统计')
    report=dict(mode='simulation',task_id=job['id'],steps=len(preview['parameters']['plan']['steps']),
        sent=job.get('sent'),verified_steps=job.get('verified_steps'),status=job['status'],
        duplicate_did_not_resend=True,real_trials=0,hardware_connected=False)
    path=Path(__file__).resolve().parent.parent/'implementation_backups/manipulation_20261016/http_simulation_report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
