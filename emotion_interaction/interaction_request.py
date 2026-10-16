"""Exact original section 3.5 wire fields; gesture extension is not approved."""
import math
import uuid

FIELDS = {"interaction_id", "label", "confidence", "occurred_at", "expires_at", "requested_action"}


def validate_v1(request):
    if set(request) != FIELDS:
        raise ValueError("INVALID_FIELDS")
    if not isinstance(request["interaction_id"], str) or not request["interaction_id"]:
        raise ValueError("INVALID_INTERACTION_ID")
    if request["label"] != "smile":
        raise ValueError("GESTURE_NOT_IN_V1_CONTRACT")
    score = request["confidence"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("INVALID_CONFIDENCE")
    if request["requested_action"] not in {"WAVE", "NOD"}:
        raise ValueError("INVALID_ACTION")
    for key in ("occurred_at", "expires_at"):
        if type(request[key]) is not int or request[key] < 0:
            raise ValueError("INVALID_TIMESTAMP")
    if request["expires_at"] <= request["occurred_at"]:
        raise ValueError("INVALID_EXPIRY")
    return request


def make_smile_request(confidence, occurred_at, requested_action="WAVE", ttl_ms=1000):
    return validate_v1({"interaction_id": str(uuid.uuid4()), "label": "smile",
                       "confidence": confidence, "occurred_at": occurred_at,
                       "expires_at": occurred_at + ttl_ms, "requested_action": requested_action})


def request_from_gesture(candidate):
    raise ValueError("GESTURE_NOT_IN_V1_CONTRACT: no robot request sent; review with B/C first")
