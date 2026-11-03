"""Smile triggers and direct execution through the existing JJT HTTP API."""

import json
import math
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


FIELDS = {"request_id", "action", "confidence", "detected_at", "expires_at"}
ACTIONS = {"WAVE", "NOD"}
DEFAULT_REQUEST_TTL_MS = 30000
MAX_REQUEST_TTL_MS = 120000
STATE_RECORD_FIELDS = ("name", "angles", "order", "motion_ms", "teaching_stage")


class JjtError(RuntimeError):
    """A JJT status, validation, transport, or execution failure."""

    def __init__(self, message, *, decision='FAILED', request_id=None):
        super().__init__(message)
        self.decision = decision
        self.request_id = request_id


def validate_trigger(trigger):
    if set(trigger) != FIELDS:
        raise ValueError("INVALID_TRIGGER_FIELDS")
    request_id = trigger["request_id"]
    if (not isinstance(request_id, str) or len(request_id) != 32
            or any(character not in "0123456789abcdef" for character in request_id)):
        raise ValueError("INVALID_REQUEST_ID")
    if trigger["action"] not in ACTIONS:
        raise ValueError("INVALID_ACTION")
    confidence = trigger["confidence"]
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("INVALID_CONFIDENCE")
    if type(trigger["detected_at"]) is not int or trigger["detected_at"] < 0:
        raise ValueError("INVALID_TIMESTAMP")
    if (type(trigger['expires_at']) is not int
            or not 0 < trigger['expires_at'] - trigger['detected_at'] <= MAX_REQUEST_TTL_MS):
        raise ValueError('INVALID_EXPIRY')
    return trigger


def make_smile_request(confidence, occurred_at, requested_action="WAVE",
                       ttl_ms=DEFAULT_REQUEST_TTL_MS):
    """Create a local trigger whose identifier already matches JJT request IDs."""
    if type(ttl_ms) is not int or not 0 < ttl_ms <= MAX_REQUEST_TTL_MS:
        raise ValueError('INVALID_REQUEST_TTL')
    return validate_trigger({
        "request_id": uuid.uuid4().hex,
        "action": requested_action,
        "confidence": confidence,
        "detected_at": occurred_at,
        "expires_at": occurred_at + ttl_ms,
    })


def request_from_gesture(candidate):
    raise ValueError("GESTURE_ACTION_NOT_CONFIGURED: gestures remain preview-only")


def parse_state_names(value):
    """Parse an ordered comma-separated JJT saved-state sequence."""
    if not isinstance(value, str):
        raise ValueError("STATE_SEQUENCE_MUST_BE_TEXT")
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError("STATE_SEQUENCE_REQUIRED")
    if any(len(name) > 60 for name in names):
        raise ValueError("STATE_NAME_TOO_LONG")
    return names


class JjtRobot:
    """Direct client for JJT's saved-state status and execution endpoints."""

    def __init__(self, base_url, *, timeout_s=5, job_timeout_s=120,
                 poll_interval_s=0.2, sleep=time.sleep, open_url=urlopen, clock_ms=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("JJT_URL_MUST_BE_HTTP_ORIGIN")
        if parsed.path not in ("", "/"):
            raise ValueError("JJT_URL_MUST_NOT_CONTAIN_PATH")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.timeout_s = timeout_s
        self.job_timeout_s = job_timeout_s
        self.poll_interval_s = poll_interval_s
        self.sleep = sleep
        self.open_url = open_url
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1000000)

    def _fresh(self, trigger):
        validate_trigger(trigger)
        now = self.clock_ms()
        if now < trigger['detected_at']:
            raise JjtError('INTERACTION_CLOCK_MISMATCH', decision='REJECTED')
        if now >= trigger['expires_at']:
            raise JjtError('INTERACTION_EXPIRED: detect a new smile; no further motion request sent',
                           decision='REJECTED')

    @staticmethod
    def _safe(status):
        if status.get('interaction_api_version') != 1:
            raise JjtError('JJT_INTERACTION_API_UPGRADE_REQUIRED', decision='REJECTED')
        if status['busy']:
            raise JjtError('JJT_BUSY', decision='REJECTED')
        safety = status.get('interaction_safety')
        if not isinstance(safety, dict) or safety.get('allowed') is not True:
            reason = safety.get('reason') if isinstance(safety, dict) else None
            raise JjtError(reason or 'DEVICE_STATE_UNKNOWN', decision='REJECTED')

    def _json(self, method, path, payload=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": self.origin})
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self.open_url(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read()
            try:
                detail = json.loads(raw.decode("utf-8")).get("error")
            except Exception:
                detail = raw.decode("utf-8", errors="replace")
            raise JjtError(f"JJT_HTTP_{error.code}: {detail or error.reason}",
                           decision='UNKNOWN' if error.code >= 500 else 'REJECTED',
                           request_id=payload.get('request_id') if payload else None) from error
        except (URLError, TimeoutError, OSError) as error:
            raise JjtError(f"JJT_UNAVAILABLE: {error}", decision='UNKNOWN',
                           request_id=payload.get('request_id') if payload else None) from error
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JjtError("JJT_INVALID_JSON_RESPONSE", decision='UNKNOWN',
                           request_id=payload.get('request_id') if payload else None) from error
        if not isinstance(value, dict):
            raise JjtError("JJT_RESPONSE_MUST_BE_OBJECT", decision='UNKNOWN',
                           request_id=payload.get('request_id') if payload else None)
        return value

    def status(self):
        status = self._json("GET", "/control/status")
        if status.get("mode") not in {"real", "simulation"}:
            raise JjtError("JJT_STATUS_INVALID_MODE")
        if type(status.get("busy")) is not bool:
            raise JjtError("JJT_STATUS_INVALID_BUSY")
        saved = status.get("saved_states")
        if (not isinstance(saved, dict) or not isinstance(saved.get("sha256"), str)
                or not isinstance(saved.get("states"), list)):
            raise JjtError("JJT_STATUS_INVALID_SAVED_STATES")
        if not isinstance(status.get("csrf"), str) or not status["csrf"]:
            raise JjtError("JJT_STATUS_INVALID_CSRF")
        return status

    @staticmethod
    def _record(state):
        if not isinstance(state, dict) or not isinstance(state.get("id"), str):
            raise JjtError("JJT_SAVED_STATE_INVALID")
        try:
            return {key: state[key] for key in STATE_RECORD_FIELDS}
        except KeyError as error:
            raise JjtError(f"JJT_SAVED_STATE_MISSING_{error.args[0].upper()}") from error

    def prepare(self, trigger, state_names):
        self._fresh(trigger)
        if not isinstance(state_names, (list, tuple)) or not state_names:
            raise ValueError("STATE_SEQUENCE_REQUIRED")
        status = self.status()
        self._safe(status)
        states = status["saved_states"]["states"]
        by_name = {}
        for state in states:
            name = state.get("name") if isinstance(state, dict) else None
            if not isinstance(name, str):
                raise JjtError("JJT_SAVED_STATE_INVALID_NAME")
            if name in by_name:
                raise JjtError(f"JJT_DUPLICATE_STATE_NAME: {name}")
            by_name[name] = state
        missing = [name for name in state_names if name not in by_name]
        if missing:
            raise JjtError("JJT_MISSING_SAVED_STATES: " + ", ".join(missing))
        steps = []
        for name in state_names:
            state = by_name[name]
            steps.append({"id": state["id"], "record": self._record(state)})
        return {
            "trigger": dict(trigger),
            "robot_url": self.origin,
            "mode": status["mode"],
            "expected_sha256": status["saved_states"]["sha256"],
            "steps": steps,
        }

    @staticmethod
    def public_plan(plan):
        return {
            "request_id": plan["trigger"]["request_id"],
            "action": plan["trigger"]["action"],
            "confidence": plan["trigger"]["confidence"],
            "expires_at": plan['trigger']['expires_at'],
            "robot_url": plan["robot_url"],
            "mode": plan["mode"],
            "steps": [step["record"] for step in plan["steps"]],
        }

    @staticmethod
    def _step_request_id(trigger_id, index):
        # 32 hex characters, deterministic for JJT idempotency without reusing an
        # identifier for two different saved-state steps.
        return uuid.uuid5(uuid.NAMESPACE_OID, f"{trigger_id}:{index}").hex

    def job(self, request_id):
        response = self._json('GET', '/control/jobs/' + request_id)
        if 'job' not in response:
            raise JjtError('JJT_INVALID_JOB_RESPONSE', decision='UNKNOWN', request_id=request_id)
        job = response['job']
        if job is not None and (not isinstance(job, dict) or job.get('id') != request_id):
            raise JjtError('JJT_INVALID_JOB_RESPONSE', decision='UNKNOWN', request_id=request_id)
        return job

    def _wait(self, request_id):
        deadline = time.monotonic() + self.job_timeout_s
        while True:
            job = self.job(request_id)
            if job is not None:
                if job.get("status") == "completed":
                    return job
                if job.get("status") == "failed":
                    raise JjtError(f"JJT_JOB_FAILED: {job.get('error', 'unknown error')}",
                                   request_id=request_id)
                if job.get('status') != 'running':
                    raise JjtError('JJT_JOB_RESULT_UNKNOWN: query or inspect; do not resend',
                                   decision='UNKNOWN', request_id=request_id)
            else:
                raise JjtError("JJT_JOB_RESULT_UNKNOWN", decision='UNKNOWN', request_id=request_id)
            if time.monotonic() >= deadline:
                raise JjtError("JJT_JOB_TIMEOUT: request was not resent", decision='UNKNOWN',
                               request_id=request_id)
            self.sleep(self.poll_interval_s)

    def execute(self, plan, *, confirmed):
        if confirmed is not True:
            return {"decision": "REJECTED", "reason": "USER_DECLINED",
                    "request_id": plan["trigger"]["request_id"]}
        completed = []
        for index, step in enumerate(plan["steps"]):
            step_request_id = self._step_request_id(plan["trigger"]["request_id"], index)
            existing = self.job(step_request_id)
            if existing is not None:
                parameters = existing.get('parameters', {})
                if (parameters.get('interaction') != plan['trigger']
                        or parameters.get('state_id') != step['id']
                        or any(parameters.get(k) != v for k, v in step['record'].items())):
                    raise JjtError('JJT_JOB_PARAMETERS_MISMATCH')
                completed.append({'state': step['record']['name'], 'request_id': step_request_id,
                                  'job': self._wait(step_request_id)})
                continue  # Read-only recovery, including after expiration; never re-POST.
            self._fresh(plan['trigger'])
            status = self.status()
            self._safe(status)
            if status["mode"] != plan["mode"]:
                raise JjtError("JJT_MODE_CHANGED")
            if status["saved_states"]["sha256"] != plan["expected_sha256"]:
                raise JjtError("JJT_SAVED_STATES_CHANGED")
            matching = next((state for state in status["saved_states"]["states"]
                             if state.get("id") == step["id"]), None)
            if matching is None or self._record(matching) != step["record"]:
                raise JjtError("JJT_SAVED_STATE_CHANGED")
            self._fresh(plan['trigger'])
            response = self._json("POST", "/control/states/execute", {
                "csrf": status["csrf"],
                "id": step["id"],
                "record": step["record"],
                "expected_sha256": plan["expected_sha256"],
                "request_id": step_request_id,
                "confirmed": True,
                "interaction": plan['trigger'],
            })
            job = response.get("job")
            if not isinstance(job, dict) or job.get("id") != step_request_id:
                raise JjtError("JJT_INVALID_JOB_RESPONSE", decision='UNKNOWN', request_id=step_request_id)
            completed.append({"state": step["record"]["name"],
                              "request_id": step_request_id,
                              "job": self._wait(step_request_id)})
        return {
            "decision": "COMMANDS_SENT",
            "motion_verified": False,
            "message": "指令流程完成；未验证实际到位，请现场确认",
            "request_id": plan["trigger"]["request_id"],
            "action": plan["trigger"]["action"],
            "mode": plan["mode"],
            "steps": completed,
        }


def confirm_and_execute(robot, trigger, state_names, *, ask=input, output=print):
    """Show the complete finite plan and require a fresh terminal confirmation."""
    plan = robot.prepare(trigger, state_names)
    output(json.dumps({"jjt_plan": robot.public_plan(plan)}, ensure_ascii=False, indent=2))
    phrase = f"EXECUTE {trigger['request_id']}"
    try:
        answer = ask(f"输入 {phrase} 确认执行以上有限计划；其他输入取消：")
    except EOFError:
        answer = ""
    if answer.strip() != phrase:
        return robot.execute(plan, confirmed=False)
    return robot.execute(plan, confirmed=True)
