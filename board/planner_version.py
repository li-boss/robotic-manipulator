"""电脑端与开发板规划代码的版本指纹（两端必须一致才使用电脑端计划）。

忽略换行符差异（Windows 上 git 可能把 \\n 转成 \\r\\n），其余逐字节比较。
"""
import hashlib
from pathlib import Path

BOARD = Path(__file__).resolve().parent

PLANNER_FILES = (
    'arm.py', 'cad_geometry.py', 'robot_model.py', 'manipulation_geometry.py', 'manipulation_ik.py',
    'manipulation_collision.py', 'manipulation_guided.py', 'manipulation_reference.py',
    'manipulation_replay.py', 'planner_version.py',
    'models/course/dofbot.urdf', 'models/manipulation/cad_profile.json',
)


def code_digest(root=BOARD):
    digest = hashlib.sha256()
    for name in PLANNER_FILES:
        data = (Path(root) / name).read_bytes().replace(b'\r\n', b'\n')
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()
