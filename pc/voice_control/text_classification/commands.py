"""读取文本分类器可输出的命令类别及其含义。"""

import json
from pathlib import Path
import re


DEFAULT_COMMANDS_PATH = Path(__file__).with_name("commands.json")
_ACTION_ID = re.compile(r"[A-Z][A-Z0-9_]*\Z")  # 用于检查命令名称的格式


def load_commands(path: str | Path = DEFAULT_COMMANDS_PATH) -> dict[str, str]:
    """返回命令标识到中文含义的映射；这里不匹配语音文字。"""
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not data:
        raise ValueError("命令配置须为非空的标识到含义映射")
    for action, description in data.items():
        if not isinstance(action, str) or not _ACTION_ID.fullmatch(action):
            raise ValueError(f"无效的命令标识：{action!r}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"命令 {action} 缺少含义说明")
    return data
