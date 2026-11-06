"""向电脑端规划服务（pc/plan_server.py）请求抓放计划；只传计算输入，不传任何硬件接口。

电脑端计划一律交给 manipulation_replay.verify_remote 在开发板复核后才可能使用。
电脑未开、版本不一致或返回格式不对时抛 PlannerUnavailable，调用方改用开发板本地规划；
电脑端按同一份代码判定“规划未通过”时抛 PlanningRejected（与开发板本地结果相同，不再重算）。
"""
import json
import time
import urllib.error
import urllib.request

from planner_version import code_digest

DEFAULT_URL = 'http://192.168.137.101:8095'


class PlannerUnavailable(Exception):
    pass


class PlanningRejected(ValueError):
    pass


class PcPlanner:
    def __init__(self, url=DEFAULT_URL, health_timeout=1.5, plan_timeout=40):
        self.url = url.rstrip('/')
        self.health_timeout, self.plan_timeout = health_timeout, plan_timeout
        self.digest = code_digest()
        self.last = None

    def _call(self, path, body=None, timeout=5):
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(self.url + path, data=data, method='GET' if body is None else 'POST',
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(32 << 20)
        except urllib.error.HTTPError as exc:
            raw = exc.read(1 << 20)
        except (OSError, ValueError) as exc:
            raise PlannerUnavailable('电脑端规划服务无法连接：' + str(getattr(exc, 'reason', exc))) from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise PlannerUnavailable('电脑端规划服务返回格式无效') from exc
        if not isinstance(value, dict):
            raise PlannerUnavailable('电脑端规划服务返回格式无效')
        return value

    def health(self):
        value = self._call('/health', timeout=self.health_timeout)
        if value.get('service') != 'jjt_pc_planner':
            raise PlannerUnavailable('该地址不是电脑端规划服务')
        if value.get('code_sha256') != self.digest:
            raise PlannerUnavailable('电脑端规划代码与开发板不一致（请两边使用同一版本 board/ 代码）')
        return value

    def plan(self, setup, arms, target, require_path, reference):
        """返回(电脑端steps, 信息)。"""
        started = time.monotonic()
        info = self.health()
        value = self._call('/plan', dict(code_sha256=self.digest, setup=setup, arms=arms, target=target,
                                         require_path=require_path, reference=reference), timeout=self.plan_timeout)
        elapsed = round(time.monotonic() - started, 2)
        if value.get('kind') == 'planning' and isinstance(value.get('error'), str):
            self.last = dict(status='rejected', elapsed_seconds=elapsed)
            raise PlanningRejected(value['error'])
        if value.get('error'):
            raise PlannerUnavailable('电脑端规划服务：' + str(value['error']))
        plan = value.get('plan')
        if not isinstance(plan, dict) or not isinstance(plan.get('steps'), list):
            raise PlannerUnavailable('电脑端未返回计划步骤')
        self.last = dict(status='ok', elapsed_seconds=elapsed)
        return plan['steps'], dict(url=self.url, host=info.get('host'), round_trip_seconds=elapsed,
                                   compute_seconds=value.get('elapsed_seconds'), numpy=info.get('numpy'))
