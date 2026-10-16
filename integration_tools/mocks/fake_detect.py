"""Section 3.2 proposal samples for A/C review, not a real detector."""
class FakeDetect:
    def detect(self, request, frame, now_ms, scenario="normal"):
        if set(request)!={"request_id","frame_id"}:
            raise ValueError("INVALID_DETECT_REQUEST_FIELDS")
        if request["frame_id"] not in {"latest",frame.frame_id}:
            return self.error("FRAME_NOT_FOUND",request["request_id"])
        if not 0 <= now_ms-frame.captured_at <= 500:
            return self.error("FRAME_EXPIRED",request["request_id"])
        if scenario not in {"normal","low_confidence","no_target"}:
            raise ValueError("UNKNOWN_DETECT_SCENARIO")
        targets=[] if scenario=="no_target" else [{"target_id":"mock-target-1","category":"可回收物",
            "confidence":.3 if scenario=="low_confidence" else .83,
            "center_x":frame.width/2,"center_y":frame.height/2}]
        return {"frame_id":frame.frame_id,"outcome":"targets" if targets else "no_target",
                "targets":targets,"inference_ms":0.0}

    @staticmethod
    def error(code, request_id):
        return {"code":code,"message":"mock detection rejected","retryable":True,"request_id":request_id}
