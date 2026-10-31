"""Run saved YOLOv8n weights through the real HTTP service on one fixed JPEG."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np
import torch
os.environ.setdefault('YOLO_CONFIG_DIR',str(Path('data/a_blocks/cache/ultralytics').resolve()))
import ultralytics
from ultralytics import YOLO

from camera_service.camera import FrameStore
from camera_service.service import VisionService, handler_for
from waste_vision.detector import Detector, ModelError
from waste_vision.yolov8_backend import EXPECTED_NAMES, YoloV8nBackend

WEIGHTS = Path('data/a_blocks/runs/yolov8n_formal_v1_cpu_20261022_01/weights/best.pt')
IMAGE = Path('data/a_blocks/formal_v1_20261022/images/val/414.jpg')
OUTPUT = Path('data/a_blocks/evaluations/yolov8n_formal_v1_cpu_http_20261023_01')
MAPPING = Path('configs/a_category_mapping.json')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timing(values):
    ordered=sorted(values); index=.95*(len(ordered)-1); low=int(index); high=min(low+1,len(ordered)-1)
    return {'n':len(values),'mean_ms':statistics.mean(values),
            'p95_ms':ordered[low]+(ordered[high]-ordered[low])*(index-low)}


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def get(url):
    with urlopen(url,timeout=15) as reply:
        return reply.status,dict(reply.headers),reply.read()


def post(url,request_id,frame_id):
    body={'request_id':request_id,'frame_id':frame_id}
    req=Request(url+'/api/v1/detect',data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type':'application/json'},method='POST')
    start=time.perf_counter()
    try:
        with urlopen(req,timeout=30) as reply:
            status=reply.status;response=json.load(reply)
    except HTTPError as exc:
        status=exc.code;response=json.load(exc)
    return {'request':body,'http_status':status,'response':response,
            'http_wall_ms':(time.perf_counter()-start)*1000}


def launch(backend,port,stdout_path,stderr_path,extra=()):
    cmd=[sys.executable,'-m','camera_service.service','--mode','image','--source',str(IMAGE),
         '--port',str(port),'--backend',backend,*extra]
    out=stdout_path.open('w',encoding='utf-8');err=stderr_path.open('w',encoding='utf-8')
    try:
        proc=subprocess.Popen(cmd,cwd=Path.cwd(),stdout=out,stderr=err)
    finally:
        out.close();err.close()
    root=f'http://127.0.0.1:{port}'
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(f'{backend} service exited {proc.returncode}: '+stderr_path.read_text(encoding='utf-8'))
        try:
            status,headers,raw=get(root+'/api/v1/frames/latest/meta')
            if status==200:return proc,root,json.loads(raw)
        except (URLError,TimeoutError):
            time.sleep(.1)
    proc.terminate();proc.wait(timeout=10)
    raise TimeoutError(f'{backend} service did not publish a frame')


def stop(proc):
    proc.terminate()
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=10)


def main():
    if OUTPUT.exists():raise FileExistsError(OUTPUT)
    if not WEIGHTS.is_file() or not IMAGE.is_file():raise FileNotFoundError('weights or image missing')
    OUTPUT.mkdir(parents=True)
    os.environ.setdefault('YOLO_CONFIG_DIR',str(Path('data/a_blocks/cache/ultralytics').resolve()))
    raw=IMAGE.read_bytes();img=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
    if img is None or img.shape[:2]!=(480,640):raise ValueError('test JPEG decode failed')
    direct_model=YOLO(str(WEIGHTS.resolve()))
    if tuple(direct_model.names[i] for i in range(4))!=EXPECTED_NAMES:raise ValueError('model classes changed')
    direct_result=direct_model.predict(source=img,imgsz=640,conf=.25,iou=.5,device='cpu',verbose=False)[0]
    if direct_result.orig_shape!=(480,640):raise ValueError('direct result is not raw image')
    direct=[{'raw_label':EXPECTED_NAMES[int(b.cls.item())], 'confidence':float(b.conf.item()),
             'bbox':[float(v) for v in b.xyxy[0].tolist()]} for b in direct_result.boxes]
    checks={};samples={};stable=[]
    port=free_port()
    proc,root,meta=launch('yolov8n',port,OUTPUT/'real_stdout.log',OUTPUT/'real_stderr.log',
                          ('--weights',str(WEIGHTS),'--model-conf','0.25','--nms-iou','0.5','--imgsz','640'))
    try:
        status,headers,fetched=get(root+'/api/v1/frames/'+meta['frame_id'])
        checks['d_raw_jpeg_same_bytes']=status==200 and fetched==raw and headers.get('X-Frame-Id')==meta['frame_id']
        (OUTPUT/'d_fetched_original.jpg').write_bytes(fetched)
        first=post(root,'real-http-001','latest');samples['first']=first
        targets=first['response'].get('targets',[])
        checks['real_response_fields']=(first['http_status']==200 and first['response']['frame_id']==meta['frame_id']
            and first['response']['coordinate_space']=='raw_camera' and first['response']['transform_version']=='raw-v1'
            and first['response']['image_width']==640 and first['response']['image_height']==480
            and first['response']['captured_at']==meta['captured_at'] and first['response']['camera_id']==meta['camera_id']
            and first['response']['simulated'] is False)
        checks['direct_equals_api_count']=len(direct)==len(targets)
        if checks['direct_equals_api_count']:
            for expected,target in zip(direct,targets):
                if expected['raw_label']!=target['raw_label'] or abs(expected['confidence']-target['confidence'])>1e-6:
                    checks['direct_equals_api_count']=False;break
                if any(abs(a-b)>1e-3 for a,b in zip(expected['bbox'],target['bbox'])):
                    checks['direct_equals_api_count']=False;break
                if abs(target['center_x']-(target['bbox'][0]+target['bbox'][2])/2)>1e-6 or abs(target['center_y']-(target['bbox'][1]+target['bbox'][3])/2)>1e-6:
                    checks['direct_equals_api_count']=False;break
        checks['unmapped_not_ready']=all(t['category'] is None and t['target_status'] in ('unmapped','low_confidence') for t in targets)
        second=post(root,'real-http-001','latest');samples['same_request_id']=second
        checks['idempotent_same_response']=second['http_status']==200 and second['response']==first['response']
        mismatch=post(root,'real-http-001',meta['frame_id']);samples['request_id_mismatch']=mismatch
        checks['request_id_conflict_400']=mismatch['http_status']==400 and mismatch['response']['code']=='BAD_REQUEST'
        missing=post(root,'invalid-frame-001','does-not-exist');samples['invalid_frame']=missing
        checks['invalid_frame_error']=missing['http_status']==503 and missing['response']['code']=='FRAME_NOT_FOUND'
        for i in range(12):stable.append(post(root,f'stable-{i:02d}',meta['frame_id']))
        checks['stable_real_inferences']=all(x['http_status']==200 and x['response']['simulated'] is False and x['response']['frame_id']==meta['frame_id'] for x in stable)
    finally:stop(proc)
    none_proc,none_root,none_meta=launch('none',free_port(),OUTPUT/'none_stdout.log',OUTPUT/'none_stderr.log')
    try:
        unavailable=post(none_root,'model-none-001',none_meta['frame_id']);samples['model_unavailable']=unavailable
        checks['none_backend_unavailable']=unavailable['http_status']==503 and unavailable['response']['code']=='MODEL_UNAVAILABLE'
    finally:stop(none_proc)
    absent=subprocess.run([sys.executable,'-m','camera_service.service','--mode','image','--source',str(IMAGE),
                          '--backend','yolov8n','--weights',str(OUTPUT/'missing.pt')],
                         capture_output=True,text=True,timeout=30)
    checks['missing_weights_startup_fails']=absent.returncode!=0 and 'MODEL_UNAVAILABLE' in absent.stderr
    (OUTPUT/'missing_weights_stderr.txt').write_text(absent.stderr,encoding='utf-8')
    corrupt=OUTPUT/'corrupt.pt';corrupt.write_bytes(b'not a model checkpoint')
    try:YoloV8nBackend(corrupt);checks['corrupt_weights_load_fails']=False
    except ModelError as exc:checks['corrupt_weights_load_fails']=exc.code=='MODEL_LOAD_FAILED'
    backend=YoloV8nBackend(WEIGHTS)
    # An old frame expires before any inference; a corrupt JPEG reaches the real backend and fails explicitly.
    store=FrameStore('test-cam',max_age_ms=1)
    old=store.publish(raw,640,480,captured_at=int(time.time()*1000)-1000)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(VisionService(store,Detector(backend,MAPPING))))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        local=f'http://127.0.0.1:{server.server_port}'
        expired=post(local,'expired-001',old.frame_id);samples['expired_frame']=expired
        checks['expired_frame_410']=expired['http_status']==410 and expired['response']['code']=='FRAME_EXPIRED'
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
    broken_store=FrameStore('test-cam',max_age_ms=0)
    broken=broken_store.publish(b'not JPEG bytes',640,480)
    broken_server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(VisionService(broken_store,Detector(backend,MAPPING))))
    thread=Thread(target=broken_server.serve_forever,daemon=True);thread.start()
    try:
        local=f'http://127.0.0.1:{broken_server.server_port}'
        failure=post(local,'inference-fail-001',broken.frame_id);samples['inference_failure']=failure
        checks['inference_failure_no_mock']=failure['http_status']==503 and failure['response']['code']=='INFERENCE_FAILED' and 'targets' not in failure['response']
    finally:broken_server.shutdown();broken_server.server_close();thread.join(timeout=5)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'weights':str(WEIGHTS.resolve()),'weights_sha256':sha(WEIGHTS.read_bytes()),
            'image':str(IMAGE.resolve()),'image_sha256':sha(raw),'device':'cpu','torch':torch.__version__,
            'ultralytics':ultralytics.__version__,'imgsz':640,'confidence':.25,'nms_iou':.5,
            'camera_metadata':meta,'direct_predictions':direct,'checks':checks,
            'latency':{'sample_count':len(stable),'inference_ms':timing([x['response']['inference_ms'] for x in stable]),
                       'processing_ms':timing([x['response']['processing_ms'] for x in stable]),
                       'http_wall_ms':timing([x['http_wall_ms'] for x in stable])},
            'limitations':['fixed old validation JPEG, not new independent scene','no actual camera tested',
                           'no human-confirmed empty scene','no GPU or Atlas inference','business garbage category unmapped']}
    (OUTPUT/'http_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUTPUT/'http_samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUTPUT/'stable_requests.json').write_text(json.dumps(stable,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':checks,'latency':report['latency'],'output':str(OUTPUT)},ensure_ascii=False,indent=2))
    if not all(checks.values()):raise AssertionError('one or more real HTTP checks failed')

if __name__=='__main__':main()
