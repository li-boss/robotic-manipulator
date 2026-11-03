"""Smile-only gate. No hardware access and no inferred physical device state."""

MAX_TTL_MS = 120000
STATE_MAX_AGE_MS = 500


def checked_trigger(value):
    fields = {'request_id', 'action', 'confidence', 'detected_at', 'expires_at'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('INTERACTION_INVALID_FIELDS')
    identifier = value['request_id']
    if (not isinstance(identifier, str) or len(identifier) != 32
            or any(c not in '0123456789abcdef' for c in identifier)):
        raise ValueError('INTERACTION_INVALID_ID')
    import math
    score = value['confidence']
    if (value['action'] not in {'WAVE', 'NOD'} or type(score) not in (int, float)
            or not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError('INTERACTION_INVALID_ACTION_OR_SCORE')
    start, end = value['detected_at'], value['expires_at']
    if (type(start) is not int or type(end) is not int or start < 0
            or not 0 < end - start <= MAX_TTL_MS):
        raise ValueError('INTERACTION_INVALID_EXPIRY')
    return dict(value)


def check_time(trigger, now_ms):
    if now_ms < trigger['detected_at']:
        raise ValueError('INTERACTION_CLOCK_MISMATCH')
    if now_ms >= trigger['expires_at']:
        raise ValueError('INTERACTION_EXPIRED')


def device_state(provider, *, real, now_ms):
    """Only an explicitly supplied B-side provider can report real safety facts."""
    unknown = dict(source='unavailable', observed_at=None,
                   holding=None, fault=None, stopped=None)
    if provider is None:
        if real:
            return dict(unknown, allowed=False, reason='DEVICE_STATE_UNKNOWN')
        value = dict(source='simulation', observed_at=now_ms,
                     holding=False, fault=False, stopped=False)
    else:
        try:
            value = provider()
        except Exception:
            return dict(unknown, allowed=False, reason='DEVICE_STATE_UNAVAILABLE')
    if not isinstance(value, dict):
        return dict(unknown, allowed=False, reason='DEVICE_STATE_UNKNOWN')
    result = {key: value.get(key) for key in unknown}
    if (not isinstance(result['source'], str) or not result['source'].strip()
            or result['source'] == 'unavailable'
            or (real and result['source'] == 'simulation')
            or any(type(result[key]) is not bool for key in ('holding', 'fault', 'stopped'))):
        return dict(result, allowed=False, reason='DEVICE_STATE_UNKNOWN')
    observed = result['observed_at']
    if type(observed) is not int or not 0 <= now_ms - observed <= STATE_MAX_AGE_MS:
        return dict(result, allowed=False, reason='DEVICE_STATE_STALE')
    for key in ('fault', 'stopped', 'holding'):
        if result[key]:
            return dict(result, allowed=False, reason='ROBOT_' + key.upper())
    return dict(result, allowed=True, reason=None)
