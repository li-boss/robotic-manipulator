"""任务事件日志（JSON Lines，按 task_id 取最新一条）。不导入硬件库。"""
import json
from pathlib import Path
import threading

import arm


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def append(self, event):
        with self.lock:
            arm.append_event(self.path, event)

    def latest(self):
        with self.lock:
            latest, errors = {}, 0
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                        latest[event["task_id"]] = event
                    except (ValueError, KeyError, TypeError):
                        errors += 1
            return list(latest.values()), errors
