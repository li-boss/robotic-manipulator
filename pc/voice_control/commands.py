"""从 JSON 口令表读取允许的整句指令。"""

import json
from pathlib import Path
import unicodedata


DEFAULT_COMMANDS_PATH = Path(__file__).with_name("commands.json")
_ALLOWED_ROUTINES = {"circle", "course"}


def normalize(text: str) -> str:
    """只忽略空白和标点，不删除否定词或其他文字。"""
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def load_commands(path: str | Path = DEFAULT_COMMANDS_PATH) -> dict[str, str]:
    """读取动作到口令列表的 JSON，并拒绝无效或跨动作重复的口令。"""
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not data:
        raise ValueError("口令表必须是非空的动作到口令列表映射")

    commands: dict[str, str] = {}
    for routine, phrases in data.items():
        if routine not in _ALLOWED_ROUTINES:
            raise ValueError(f"口令表包含未知动作：{routine}")
        if not isinstance(phrases, list) or not phrases:
            raise ValueError(f"动作 {routine} 必须有非空口令列表")
        for phrase in phrases:
            if not isinstance(phrase, str) or not normalize(phrase):
                raise ValueError(f"动作 {routine} 包含空口令或非文字口令")
            key = normalize(phrase)
            if key in commands and commands[key] != routine:
                raise ValueError(f"口令 {phrase} 同时映射到两个动作")
            commands[key] = routine
    return commands


def parse_command(text: str, commands_path: str | Path = DEFAULT_COMMANDS_PATH) -> str | None:
    """只匹配完整句子；未命中时返回 None。"""
    return load_commands(commands_path).get(normalize(text))
