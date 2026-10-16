"""Deterministic D-only scenarios; no hardware and no claims of A/B/C integration."""
import argparse
import json
from pathlib import Path
from emotion_interaction.gesture_policy import GesturePolicy
from emotion_interaction.interaction_request import make_smile_request, request_from_gesture
from integration_tools.mocks.fake_robot import FakeRobot
from integration_tools.mocks.fake_camera import FakeCamera
from integration_tools.mocks.fake_detect import FakeDetect
from integration_tools.mocks.fake_event_sink import FakeEventSink
import uuid


def dependencies_scenario(scenario):
    camera=FakeCamera()
    frame=camera.read(1000,{"expired_frame":"expired","camera_down":"camera_down"}.get(scenario,"normal"))
    if scenario=="camera_down":
        assert frame["code"]=="CAMERA_UNAVAILABLE"
        return {"camera_error":frame,"mock_action_count":0}
    if scenario=="net_down":
        path=Path("logs")/f"mock-sink-{uuid.uuid4().hex}.sqlite3"
        path.parent.mkdir(exist_ok=True)
        event={"event_id":"mock-event-001","task_id":"mock-task-001","seq":1,
               "occurred_at":1000,"event_type":"STARTED","device_state":"BUSY"}
        sink=FakeEventSink(path)
        try:
            sink.online=False
            offline=sink.receive(event)
            sink.online=True
            restored=sink.receive(event)
            duplicate=sink.receive(event)
            assert offline[0]==503 and restored[0]==202 and duplicate[1]["duplicate"]
            return {"event":event,"offline":offline,"restored":restored,"duplicate":duplicate,
                    "note":"same event replayed by test driver; B persistent outbox not implemented"}
        finally: sink.close()
    detection=FakeDetect().detect({"request_id":"mock-detect-1","frame_id":frame.frame_id},frame,1000,scenario if scenario in {"low_confidence","no_target"} else "normal")
    if scenario=="expired_frame": assert detection["code"]=="FRAME_EXPIRED"
    elif scenario=="no_target": assert detection["targets"]==[]
    else: assert detection["targets"][0]["confidence"]<.5
    return {"frame":frame.metadata(),"detection":detection,"mock_action_count":0,
            "note":"data sample only; no C scheduling or fallback positioning implemented"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["gesture","normal","robot_busy","holding","fault","stopped","expired","duplicate","low_confidence","no_target","camera_down","expired_frame","net_down"], default="gesture")
    args=parser.parse_args()
    states={"robot_busy":"BUSY","holding":"HOLDING","fault":"FAULT","stopped":"STOPPED"}
    robot=FakeRobot(states.get(args.scenario,"IDLE"))
    request=None
    if args.scenario in {"low_confidence","no_target","camera_down","expired_frame","net_down"}:
        result=dependencies_scenario(args.scenario)
    elif args.scenario=="gesture":
        policy=GesturePolicy()
        candidate=None
        for i in range(5):
            candidate=policy.update(frame_id=f"mock-{i}",captured_at=1000+i*10,
                now_ms=1000+i*10,gesture="like",confidence=.91)
        try:
            request_from_gesture(candidate)
        except ValueError as error:
            result={"decision":"LOCAL_ONLY","reason":str(error),"candidate":candidate}
        assert candidate is not None and not robot.actions
    else:
        request=make_smile_request(.91,1000)
        request["interaction_id"]="mock-interaction-001"
        result=robot.interact(request,2000 if args.scenario=="expired" else 1100)
        if args.scenario=="duplicate":
            robot.interact(request,1200)
        assert len(robot.actions)==(1 if args.scenario in {"normal","duplicate"} else 0)
    report={"contract_version":"1","scenario":args.scenario,"task_id":None,
            "interaction_id":request["interaction_id"] if request else None,
            "request":request,"result":result,"mock_action_count":len(robot.actions),
            "scope":"D-only offline interaction; ACCEPTED is not hardware completion"}
    output=Path("logs")/f"mock-{args.scenario}.json"
    output.parent.mkdir(exist_ok=True)
    report["log_path"]=str(output.resolve())
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
