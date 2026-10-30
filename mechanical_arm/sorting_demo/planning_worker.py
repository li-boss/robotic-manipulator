"""Bounded caller-owned pure planning process. Never connects to arm hardware."""
import json
import sys
import types
import time

forbidden=types.ModuleType('Arm_Lib')
def no_hardware(*args,**kwargs):
    raise RuntimeError('Planning worker must not instantiate arm hardware')
forbidden.Arm_Device=no_hardware
sys.modules['Arm_Lib']=forbidden

from manipulation_geometry import build_plan


if __name__=='__main__':
    started=time.monotonic()
    try:
        fields=json.load(sys.stdin)
        def progress(stage):
            print(json.dumps(dict(stage=stage,elapsed_seconds=round(time.monotonic()-started,3)),ensure_ascii=False),file=sys.stderr,flush=True)
        plan=build_plan(fields['setup'],fields['arms'],fields['target'],fields['require_path'],progress=progress,reference=fields.get('reference'))
        print(json.dumps(dict(plan=plan,elapsed_seconds=round(time.monotonic()-started,3)),ensure_ascii=False,allow_nan=False),flush=True)
    except Exception as exc:
        print(json.dumps(dict(error=str(exc),elapsed_seconds=round(time.monotonic()-started,3)),ensure_ascii=False),flush=True)
        raise SystemExit(2)
