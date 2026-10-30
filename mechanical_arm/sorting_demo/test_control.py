"""假控制器测试，不导入Arm_Lib、不连接设备。"""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from control import Control, arm


class Fake:
    calls = []
    angles = [90, 50, 40, 40, 89, 80]
    fail = False
    read_fail = False
    wait = None

    def __init__(self, config, real=False):
        self.config = config
        self.real = real

    def read_report(self):
        return [dict(id=i, angle=a, read_ok=not self.read_fail, error='failed' if self.read_fail else None)
                for i, a in enumerate(self.angles, 1)]

    def write(self, pairs, retries=0):
        checked = [(i, arm.angle(self.config, i, a)) for i, a in pairs]
        arm.timing(self.config)
        self.calls.append(checked)
        type(self).retries_requested = retries
        self.last_retries = list(getattr(type(self), 'simulated_retries', []))
        if self.wait:
            self.wait.wait(2)
        if self.fail:
            raise RuntimeError('write failed')


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'arm_playground').mkdir()
        (self.root/'camera_preview').mkdir()
        for name in ['AGENTS.md', '机械臂操作准则.md', 'arm_playground/pose_setup.py',
                     'arm_playground/joint_check.py', 'camera_preview/server.py']:
            (self.root/name).write_text('test audit fixture', encoding='utf-8')
        self.path = self.root/'arm_playground/config.json'
        self.cfg = dict(hardware_ready=True, motion_ms=1500, settle_ms=300,
                        joint_limits={str(i): [90, 250] if i==5 else [10, 170] for i in range(1,7)},
                        poses={}, targets={'yellow': {}}, gripper={})
        self.write_config()
        Fake.calls, Fake.fail, Fake.read_fail, Fake.wait = [], False, False, None
        Fake.angles = [90, 50, 40, 40, 89, 80]
        self.c = Control(self.path, real=True, factory=Fake, root=self.root, lock_factory=nullcontext)

    def tearDown(self):
        if Fake.wait:
            Fake.wait.set()
        self.wait_done()
        self.tmp.cleanup()

    def write_config(self):
        self.path.write_text(json.dumps(self.cfg), encoding='utf-8')

    def preview(self, **kw):
        return self.c.preview(dict(operation='move', joint=1, angle=100, motion_ms=1500, **kw))

    def execute(self, p):
        return self.c.execute(dict(token=p['token'], confirmed=True))

    def wait_done(self):
        for _ in range(500):
            if not self.c.busy:
                return
            time.sleep(.002)
        self.fail('worker did not finish')

    def direct(self, token='a'*32):
        return self.c.direct_move(dict(request_id=token, confirmed=True, joint=1, angle=100, motion_ms=1500))

    def test_direct_without_reads_dedup_and_restart(self):
        with patch.object(Fake, 'read_report', side_effect=AssertionError('must not read')):
            self.direct();self.wait_done();self.direct()
            replacement=Control(self.path,real=True,factory=Fake,root=self.root,lock_factory=nullcontext)
            replacement.direct_move(dict(request_id='a'*32,confirmed=True,joint=1,angle=100,motion_ms=1500))
            self.assertEqual(Fake.calls,[[(1,100)]])
            self.assertEqual(self.c.rows,[])
            self.assertEqual(self.c.commanded,{'1':100})

    def test_direct_move_requests_retries_and_logs_recovered_write(self):
        Fake.simulated_retries=[dict(joint=1,angle=100,attempt=1,error='Arm_serial_servo_write I2C error')]
        try:
            self.direct();self.wait_done()
        finally:Fake.simulated_retries=[]
        self.assertEqual(self.c.job['status'],'completed');self.assertEqual(Fake.retries_requested,2)
        log=[json.loads(x) for x in self.c.log.read_text().splitlines()]
        self.assertTrue(any(e['kind']=='write_retry' and e['joint']==1 and e['attempt']==1 for e in log))

    def test_direct_boundaries_invalid_and_unready(self):
        import secrets
        for joint in range(1,7):
            lo,hi=self.cfg['joint_limits'][str(joint)]
            for value in [lo,hi]:
                self.c.direct_move(dict(request_id=secrets.token_hex(16),confirmed=True,joint=joint,angle=value,motion_ms=1500));self.wait_done()
            for value in [lo-1,hi+1,None,True,90.5,'90']:
                with self.assertRaises(ValueError):
                    self.c.direct_move(dict(request_id=secrets.token_hex(16),confirmed=True,joint=joint,angle=value,motion_ms=1500))
        before=len(Fake.calls)
        for duration in [None,True,499,10001,1500.5,'1500']:
            with self.assertRaises(ValueError):
                self.c.direct_move(dict(request_id='e'*32,confirmed=True,joint=1,angle=100,motion_ms=duration))
        self.cfg['hardware_ready']=False;self.write_config()
        with self.assertRaises(ValueError):self.direct()
        self.assertEqual(len(Fake.calls),before)

    def test_direct_busy_binding_failure_no_retry(self):
        Fake.wait=threading.Event();Fake.fail=True
        self.direct();self.direct()
        with self.assertRaises(ValueError):self.direct('b'*32)
        with self.assertRaises(ValueError):
            self.c.direct_move(dict(request_id='a'*32,confirmed=True,joint=1,angle=101,motion_ms=1500))
        Fake.wait.set();self.wait_done()
        self.assertEqual(Fake.calls,[[(1,100)]])
        self.assertEqual(self.c.job['status'],'failed')
        self.direct();self.assertEqual(Fake.calls,[[(1,100)]])

    def make_state(self, **overrides):
        fields=dict(name='观察按钮',angles=[90,50,40,40,90,80],order=[6,2,3,4,1,5],motion_ms=1500,
                    teaching_stage='reference',expected_sha256=self.c.states.status()['sha256'])
        fields.update(overrides)
        p=self.c.analyze_state(fields)
        return self.c.save_state(dict(token=p['token']))['status']

    def state_request(self, status, token='c'*32):
        item=status['states'][0]
        from saved_states import checked
        return dict(id=item['id'],expected_sha256=status['sha256'],request_id=token,
                    confirmed=True,record=checked(self.cfg,item))

    def test_state_analyze_save_no_reads_no_motion_order_and_dedup(self):
        with patch.object(Fake,'read_report',side_effect=AssertionError('no reads')):
            status=self.make_state();self.assertEqual(Fake.calls,[])
            request=self.state_request(status)
            self.c.state_move(request);self.wait_done();self.c.state_move(request)
            self.assertEqual(Fake.calls,[[(6,80)],[(2,50)],[(3,40)],[(4,40)],[(1,90)],[(5,90)]])
            replacement=Control(self.path,real=True,factory=Fake,root=self.root,lock_factory=nullcontext)
            replacement.state_move(request)
            self.assertEqual(len(Fake.calls),6)
            self.assertEqual(self.c.job['sent'],6)

    def test_state_reject_invalid_last_target_orders_time_and_unready(self):
        for angles in [[90,50,40,40,89,80],[90,50,40,40,90,171],[90,50,40,40,90,None],
                       [90,50,40,40,90,True],[90,50,40,40,90,90.5],[90,50,40,40,90]]:
            with self.assertRaises(ValueError):self.make_state(angles=angles)
        for order in [[1,2,3,4,5],[1,2,3,4,5,5],[True,2,3,4,5,6],[1,2,3,4,5,7]]:
            with self.assertRaises(ValueError):self.make_state(order=order)
        for duration in [499,10001,True,None]:
            with self.assertRaises(ValueError):self.make_state(motion_ms=duration)
        self.assertEqual(Fake.calls,[])
        status=self.make_state();self.cfg['hardware_ready']=False;self.write_config()
        with self.assertRaises(ValueError):self.c.state_move(self.state_request(status))
        self.assertEqual(Fake.calls,[])

    def test_state_edit_backup_stale_revision_and_display_binding(self):
        status=self.make_state();item=status['states'][0]
        wrong=self.state_request(status);wrong['record']['angles'][0]=100
        with self.assertRaises(ValueError):self.c.state_move(wrong)
        updated=self.make_state(id=item['id'],name='新名称',order=[1,2,3,4,5,6])
        self.assertEqual(len(updated['states']),1)
        self.assertTrue(list(self.path.parent.glob('manual_states.json.before_*')))
        with self.assertRaises(ValueError):self.c.state_move(self.state_request(status))
        with self.assertRaises(ValueError):self.make_state(expected_sha256=status['sha256'])
        self.assertEqual(Fake.calls,[])

    def test_state_batch_failure_stops_remaining_and_busy(self):
        status=self.make_state();request=self.state_request(status)
        Fake.wait=threading.Event();Fake.fail=True
        self.c.state_move(request)
        with self.assertRaises(ValueError):self.direct()
        with self.assertRaises(ValueError):self.make_state()
        self.c.state_move(request)
        Fake.wait.set();self.wait_done()
        self.assertEqual(Fake.calls,[[(6,80)]])
        self.assertEqual(self.c.job['status'],'failed')
        self.c.state_move(request);self.assertEqual(Fake.calls,[[(6,80)]])

    def test_state_config_change_after_first_write_stops(self):
        status=self.make_state()
        original=Fake.write
        def write(controller,pairs,retries=0):
            original(controller,pairs,retries)
            self.cfg['settle_ms']=400;self.write_config()
        with patch.object(Fake,'write',write):
            self.c.state_move(self.state_request(status));self.wait_done()
        self.assertEqual(Fake.calls,[[(6,80)]])
        self.assertEqual(self.c.job['status'],'failed')

    def test_state_rechecks_changed_last_joint_before_any_write(self):
        status=self.make_state();request=self.state_request(status)
        self.cfg['joint_limits']['6']=[10,70];self.write_config()
        with self.assertRaises(ValueError):self.c.state_move(request)
        self.assertEqual(Fake.calls,[])

    def test_state_analysis_invalidated_by_config_and_delete_backup(self):
        fields=dict(name='姿态',angles=[90,50,40,40,90,80],order=[1,2,3,4,5,6],motion_ms=1500,
                    expected_sha256=self.c.states.status()['sha256'])
        p=self.c.analyze_state(fields);self.cfg['settle_ms']=400;self.write_config()
        with self.assertRaises(ValueError):self.c.save_state(dict(token=p['token']))
        status=self.make_state()
        result=self.c.delete_state(dict(id=status['states'][0]['id'],expected_sha256=status['sha256']))
        self.assertEqual(result['status']['states'],[]);self.assertTrue(Path(result['backup']).exists())
        self.assertEqual(Fake.calls,[])

    def test_read_and_preview_do_not_write(self):
        self.assertEqual(self.c.read()['status']['joints'][4]['angle'],89)
        p=self.preview()
        self.assertEqual(p['parameters']['angle'],100)
        self.assertEqual(Fake.calls,[])

    def test_duplicate_exec_writes_once(self):
        p=self.preview();self.execute(p);self.wait_done();self.execute(p)
        self.assertEqual(Fake.calls,[[(1,100)]])
        self.assertEqual(self.c.job['status'],'completed')

    def test_expired_or_unconfirmed_never_writes(self):
        p=self.preview()
        with self.assertRaises(ValueError):self.c.execute(dict(token=p['token']))
        self.c.pending['expires']=0
        with self.assertRaises(ValueError):self.execute(p)
        self.assertEqual(Fake.calls,[])

    def test_config_and_rule_change_reject(self):
        for change in ('config','rule'):
            p=self.preview()
            if change=='config':
                self.cfg['settle_ms']=400;self.write_config()
            else:(self.root/'AGENTS.md').write_text('changed')
            with self.assertRaises(ValueError):self.execute(p)
        self.assertEqual(Fake.calls,[])

    def test_changed_start_rejects(self):
        p=self.preview();Fake.angles[0]=91;self.execute(p);self.wait_done()
        self.assertEqual(Fake.calls,[]);self.assertEqual(self.c.job['status'],'failed')

    def test_busy_blocks_and_duplicate_does_not_queue(self):
        Fake.wait=threading.Event();p=self.preview();self.execute(p)
        self.execute(p)
        with self.assertRaises(ValueError):self.c.read()
        with self.assertRaises(ValueError):self.preview()
        Fake.wait.set();self.wait_done();self.assertEqual(len(Fake.calls),1)

    def test_write_exception_never_retries(self):
        Fake.fail=True;p=self.preview();self.execute(p);self.wait_done();self.execute(p)
        self.assertEqual(len(Fake.calls),1);self.assertEqual(self.c.job['status'],'failed')

    def test_failed_read_never_writes(self):
        Fake.read_fail=True
        with self.assertRaises(ValueError):self.preview()
        self.assertEqual(Fake.calls,[])

    def test_readback_tolerance_does_not_change_target(self):
        with self.assertRaises(ValueError):self.c.preview(dict(operation='move',joint=5,angle=89,motion_ms=1500))
        p=self.c.preview(dict(operation='move',joint=5,angle=90,motion_ms=1500))
        self.execute(p);self.wait_done();self.assertEqual(Fake.calls,[[(5,90)]])

    def test_all_six_config_bounds_and_invalid_inputs(self):
        for i in range(1,7):
            lo,hi=self.cfg['joint_limits'][str(i)]
            for a in [lo,hi]:self.c.preview(dict(operation='move',joint=i,angle=a,motion_ms=1500))
            for a in [lo-1,hi+1,None,True,'90',90.5,float('nan'),float('inf')]:
                with self.assertRaises(ValueError):self.c.preview(dict(operation='move',joint=i,angle=a,motion_ms=1500))
        for v in [0,499,10001,True,1500.5]:
            with self.assertRaises(ValueError):self.c.preview(dict(operation='move',joint=1,angle=100,motion_ms=v))
        self.assertEqual(Fake.calls,[])

    def test_expanded_missing_limits_and_not_ready(self):
        for limits in ([0,181], None):
            self.cfg['joint_limits']['1']=limits;self.write_config()
            with self.assertRaises(ValueError):self.preview()
        self.cfg['joint_limits']['1']=[10,170];self.cfg['hardware_ready']=False;self.write_config()
        with self.assertRaises(ValueError):self.preview()
        self.assertEqual(Fake.calls,[])

    def test_save_requires_manual_legal_targets_and_backs_up(self):
        with self.assertRaises(ValueError):self.c.preview(dict(operation='save',name='ready',angles=Fake.angles[:5]))
        p=self.c.preview(dict(operation='save',name='ready',angles=[90,50,40,40,90]))
        before=self.path.read_bytes();self.execute(p);self.wait_done()
        self.assertEqual(self.c.config()['poses']['ready'],[90,50,40,40,90])
        self.assertEqual(Path(self.c.job['backup']).read_bytes(),before)
        self.assertEqual(Fake.calls,[])

    def test_simulation_cannot_save(self):
        self.c.real=False
        with self.assertRaises(ValueError):self.c.preview(dict(operation='save',name='gripper.open',angles=[80]))

    def test_restart_token_not_reusable(self):
        p=self.preview()
        other=Control(self.path,real=True,factory=Fake,root=self.root,lock_factory=nullcontext)
        with self.assertRaises(ValueError):other.execute(dict(token=p['token'],confirmed=True))
        self.assertEqual(Fake.calls,[])

    def test_log_failure_prevents_write(self):
        p=self.preview()
        with patch.object(self.c,'record',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.execute(p)
        self.assertEqual(Fake.calls,[])

    def test_http_controls_require_origin_token_and_confirmation(self):
        import importlib.util
        import io
        from email.message import Message
        from types import SimpleNamespace
        spec=importlib.util.spec_from_file_location('web_control_test',Path(__file__).parent/'server.py')
        web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
        service=SimpleNamespace(camera=None,control=self.c)
        handler_type=web.demo_handler(service)
        def post(path,body,origin='http://192.168.137.100:8080'):
            handler=handler_type.__new__(handler_type);handler.path=path
            raw=json.dumps(body).encode();handler.rfile=io.BytesIO(raw)
            handler.headers=Message();handler.headers['Host']='192.168.137.100:8080'
            handler.headers['Content-Type']='application/json';handler.headers['Content-Length']=str(len(raw))
            if origin:handler.headers['Origin']=origin
            result=[];handler.send_json=lambda status,value:result.append((status,value))
            handler.do_POST();return result[0]
        for origin,csrf in [(None,self.c.csrf),('http://other.test',self.c.csrf),('http://192.168.137.100:8080','bad')]:
            self.assertEqual(post('/control/read',dict(csrf=csrf),origin)[0],403)
        p=post('/control/preview',dict(csrf=self.c.csrf,operation='move',joint=1,angle=100,motion_ms=1500))
        self.assertEqual(p[0],200);self.assertEqual(Fake.calls,[])
        self.assertEqual(post('/control/execute',dict(csrf=self.c.csrf,token=p[1]['token']))[0],400)
        self.assertEqual(Fake.calls,[])


if __name__=='__main__':unittest.main()
