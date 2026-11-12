"""把转写文字匹配到本项目已定义的两套跳舞动作。"""

import unicodedata


_COMMANDS = {
    "机械臂画圈": "circle",
    "机械臂跳课程舞": "course",
}


def parse_command(text: str) -> str | None:
    """仅忽略空白和标点；完整口令匹配失败时返回 None。"""
    normalized = "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )
    return _COMMANDS.get(normalized)
