
"""Business categories are explicit and independent from model labels."""
import json
from pathlib import Path

BUSINESS_CATEGORIES = {"可回收物", "厨余垃圾", "有害垃圾", "其他垃圾"}


def load_mapping(path: str | Path) -> dict[str, str | None]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mapping must be a JSON object")
    for label, category in data.items():
        if not isinstance(label, str) or (category is not None and category not in BUSINESS_CATEGORIES):
            raise ValueError(f"invalid mapping for {label}")
    return data
