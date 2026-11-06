"""电脑端 ↔ 开发板通信库。

    from armlink import BoardClient, BlockDetection, GestureEvent
"""
from .client import BoardClient, BoardError, DEFAULT_BOARD
from .types import BlockDetection, Frame, GestureEvent

__all__ = ['BoardClient', 'BoardError', 'DEFAULT_BOARD', 'BlockDetection', 'Frame', 'GestureEvent']
