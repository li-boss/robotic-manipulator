"""Section 3.1 metadata + separate binary payload; no image in business JSON."""
from dataclasses import dataclass
import uuid


@dataclass(frozen=True)
class Frame:
    frame_id: str
    captured_at: int
    camera_id: str
    width: int
    height: int
    encoding: str
    payload: bytes

    def metadata(self):
        return {k:getattr(self,k) for k in ("frame_id","captured_at","camera_id","width","height","encoding")}


class FakeCamera:
    def __init__(self, width=64, height=48):
        if min(width,height)<1:
            raise ValueError("INVALID_DIMENSIONS")
        self.width,self.height=width,height
        self.session=uuid.uuid4().hex
        self.index=0

    def read(self, now_ms, scenario="normal"):
        if scenario=="camera_down":
            return {"code":"CAMERA_UNAVAILABLE","message":"mock frame timeout",
                    "retryable":True,"request_id":f"camera-{self.session}-{self.index}"}
        if scenario not in {"normal","expired"}:
            raise ValueError("UNKNOWN_CAMERA_SCENARIO")
        self.index+=1
        return Frame(f"{self.session}-{self.index}",now_ms-501 if scenario=="expired" else now_ms,
                     "mock-camera",self.width,self.height,"bgr8",bytes(self.width*self.height*3))
