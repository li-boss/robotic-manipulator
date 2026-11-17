"""读取人工标注的命令样本；无命令显式标为 NO_COMMAND。"""

import json
from pathlib import Path
import unicodedata

from .commands import load_commands


def load_examples(path: str | Path) -> list[tuple[str, str]]:
    actions = set(load_commands())
    examples = []
    seen = set()
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict) or set(item) != {"text", "action"}:
            raise ValueError(f"{path}:{line_number} 须包含 text 和 action")
        text, action = item["text"], item["action"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{path}:{line_number} 缺少有效文字")
        if not isinstance(action, str) or action not in actions:
            raise ValueError(f"{path}:{line_number} 未配置的命令：{action!r}")
        key = identity(text)
        if key in seen:
            raise ValueError(f"{path}:{line_number} 重复的文字：{text}")
        seen.add(key)
        examples.append((text.strip(), action))
    if not examples:
        raise ValueError(f"标注数据为空：{path}")
    return examples


def identity(text: str) -> str:
    """仅用于发现跨数据集的相同句子，不参与模型预测。"""
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def check_disjoint(*datasets: list[tuple[str, str]]) -> None:
    seen = set()
    for index, examples in enumerate(datasets, 1):
        keys = {identity(text) for text, _ in examples}
        duplicates = seen & keys
        if duplicates:
            raise ValueError(
                f"第 {index} 份数据与之前的数据有重复句子：{sorted(duplicates)[0]}"
            )
        seen.update(keys)
