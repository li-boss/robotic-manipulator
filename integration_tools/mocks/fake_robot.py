"""Section 3.5 interaction subset; not a full B implementation."""
from emotion_interaction.interaction_request import validate_v1


class FakeRobot:
    def __init__(self, state="IDLE", holding=False):
        self.state, self.holding = state, holding
        self.decisions = {}
        self.actions = []

    def interact(self, request, now_ms):
        validate_v1(request)
        identity = request["interaction_id"]
        if identity in self.decisions:
            previous, result = self.decisions[identity]
            if request != previous:
                return {"decision": "REJECTED", "reason": "INTERACTION_ID_CONFLICT"}
            return dict(result)
        reason = None
        if now_ms < request["occurred_at"]:
            reason = "FUTURE_REQUEST"
        elif now_ms >= request["expires_at"]:
            reason = "EXPIRED"
        elif self.holding or self.state == "HOLDING":
            reason = "ROBOT_HOLDING"
        elif self.state != "IDLE":
            reason = {"BUSY": "ROBOT_BUSY", "FAULT": "ROBOT_FAULT", "STOPPING": "ROBOT_STOPPING", "STOPPED": "ROBOT_STOPPED"}.get(self.state, "DEVICE_STATE_UNKNOWN")
        result = {"decision": "REJECTED", "reason": reason} if reason else {"decision": "ACCEPTED"}
        if reason is None:
            self.actions.append(dict(request))
            self.state = "BUSY"
        self.decisions[identity] = (dict(request), result)
        return dict(result)
