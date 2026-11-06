"""电脑端抓放规划服务：替开发板做逆解与路径搜索，只做纯计算。

开发板在每次“生成计划”时把配置、标定、目标和九点参考一起发来；这里用同一份
board/ 规划代码计算，返回各阶段关节指令。开发板收到后自己重放复核（角度范围、
位置方向、碰撞扫掠、衔接切角），不通过就改用开发板本地规划。本程序不导入舵机库、
不连接机械臂、不发送任何运动，也不保存现场数据。

用法（与开发板网线相连的电脑，有线网卡 192.168.137.101）：
    python pc/plan_server.py
关掉本程序，开发板自动回到本地规划（较慢，功能不变）。
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import socket
import sys
import threading
import time
import types

BOARD = Path(__file__).resolve().parents[1] / 'board'
sys.path.insert(0, str(BOARD))

# 与 board/planning_worker.py 相同：规划进程里禁止创建舵机对象。
_forbidden = types.ModuleType('Arm_Lib')


def _no_hardware(*args, **kwargs):
    raise RuntimeError('电脑端规划服务不得连接机械臂')


_forbidden.Arm_Device = _no_hardware
sys.modules['Arm_Lib'] = _forbidden

import numpy  # noqa: E402

from manipulation_geometry import build_plan  # noqa: E402
from planner_version import code_digest  # noqa: E402

MAX_BODY = 8 << 20
DIGEST = code_digest()
BUSY = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = 'jjt-pc-planner'

    def log_message(self, fmt, *args):
        print(time.strftime('%H:%M:%S'), self.address_string(), fmt % args, flush=True)

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != '/health':
            return self.reply(404, dict(error='未知地址'))
        self.reply(200, dict(service='jjt_pc_planner', hardware=False, code_sha256=DIGEST,
                             host=socket.gethostname(), python=platform.python_version(),
                             numpy=numpy.__version__, busy=BUSY.locked()))

    def do_POST(self):
        if self.path != '/plan':
            return self.reply(404, dict(error='未知地址'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('请求大小无效')
            fields = json.loads(self.rfile.read(length))
            if not isinstance(fields, dict):
                raise ValueError('请求格式无效')
        except ValueError as exc:
            return self.reply(400, dict(error=str(exc), kind='bad_request'))
        if fields.get('code_sha256') != DIGEST:
            return self.reply(409, dict(error='电脑端规划代码与开发板不一致', kind='version', code_sha256=DIGEST))
        if fields.get('reference') is None:
            return self.reply(400, dict(error='电脑端规划只用于九点引导模式', kind='bad_request'))
        if not BUSY.acquire(blocking=False):
            return self.reply(503, dict(error='电脑端正在计算上一份计划', kind='busy'))
        started = time.monotonic()
        try:
            plan = build_plan(fields['setup'], fields['arms'], fields['target'], fields['require_path'] is True,
                              reference=fields['reference'])
        except ValueError as exc:
            # 同一份代码的确定性判定：开发板本地会得到相同结论。
            return self.reply(200, dict(error=str(exc), kind='planning',
                                        elapsed_seconds=round(time.monotonic() - started, 3)))
        except Exception as exc:  # 输入或环境问题：让开发板改用本地规划。
            return self.reply(500, dict(error=type(exc).__name__ + ': ' + str(exc), kind='internal'))
        finally:
            BUSY.release()
        elapsed = round(time.monotonic() - started, 3)
        print(f'计划完成：{len(plan["steps"])}步，用时{elapsed}秒', flush=True)
        self.reply(200, dict(plan=dict(steps=plan['steps']), elapsed_seconds=elapsed))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='0.0.0.0', help='监听地址（默认所有网卡）')
    parser.add_argument('--port', type=int, default=8095)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'电脑端规划服务：http://{args.host}:{args.port}  代码指纹 {DIGEST[:16]}  numpy {numpy.__version__}', flush=True)
    print('只做纯计算，不连接机械臂。Ctrl+C 退出（退出后开发板自动用本地规划）。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
