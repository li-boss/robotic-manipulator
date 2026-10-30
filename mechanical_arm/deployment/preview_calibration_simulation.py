"""Loopback-only visual QA with a synthetic board and fake servo factory."""
from http.server import ThreadingHTTPServer
import argparse
from pathlib import Path
import sys
import threading
import time
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sorting_demo'))
forbidden=types.ModuleType('Arm_Lib')
def no_device(*args,**kwargs):raise AssertionError('Real Arm_Device forbidden in visual QA')
forbidden.Arm_Device=no_device
sys.modules['Arm_Lib']=forbidden

from server import demo_handler
from test_charuco_calibration import SessionTests,StatefulFake


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--position',action='store_true',help='seed certified synthetic optics/mount in the temporary fake fixture')
    args=parser.parse_args()
    fixture=SessionTests();fixture.setUp()
    if args.position:
        fixture.test_apply_hand_eye_table_height_and_regeneration_preserve_sources()
    fixture.control.real=False
    assert fixture.control.factory is StatefulFake
    fixture.demo.manipulation=fixture.manipulation
    camera=fixture.camera;camera.stop=threading.Event()
    def update():
        while not camera.stop.wait(.1):
            with camera.condition:
                camera.updated=time.monotonic();camera.sequence+=1;camera.condition.notify_all()
    threading.Thread(target=update,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',8093),demo_handler(fixture.demo))
    print('http://127.0.0.1:8093/calibration | SYNTHETIC CAMERA, FAKE ARM, SIMULATION ONLY',flush=True)
    try:server.serve_forever()
    finally:
        camera.stop.set();server.server_close()
        print('Synthetic manual commands:',StatefulFake.calls,flush=True)
        assert fixture.control.factory is StatefulFake
        fixture.tmp.cleanup()
