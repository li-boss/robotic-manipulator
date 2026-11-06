"""电脑端与开发板之间传递的数据结构。坐标一律是原图像素（左上角为原点，u 向右、v 向下）。"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


@dataclass
class BlockDetection:
    """一块方块的识别结果（方块识别同学的模型输出这个）。

    两种写法二选一（推荐 corners）：
      corners  顶面四个角，沿轮廓顺序（顺/逆时针都行）。第1→第2角那条边决定夹爪方向。
      bbox     [left, top, right, bottom] 外接框；方向用 angle_deg（顶面一条边在图中的角度，
               0°=水平，顺时针为正），不给就按 0°。
    center    顶面中心（可选）。corners 时默认取对角线交点，bbox 时默认框中心。
              注意：是“顶面”中心，不是整个方块轮廓（含侧面）的质心。
    score     置信度 0～1，低于网页上设置的下限会被开发板拒绝。
    label     类别名（可选，例如 'yellow'），只用于日志。
    """
    corners: Optional[Sequence[Point]] = None
    bbox: Optional[Sequence[float]] = None
    angle_deg: Optional[float] = None
    center: Optional[Point] = None
    score: float = 1.0
    label: Optional[str] = None

    def to_dict(self):
        if self.corners is None and self.bbox is None:
            raise ValueError('BlockDetection 需要 corners 或 bbox')
        out = dict(score=float(self.score))
        if self.corners is not None:
            corners = [[float(u), float(v)] for u, v in self.corners]
            if len(corners) != 4:
                raise ValueError('corners 须为 4 个点')
            out['corners'] = corners
        else:
            out['bbox'] = [float(x) for x in self.bbox]
            if self.angle_deg is not None:
                out['angle_deg'] = float(self.angle_deg)
        if self.center is not None:
            out['center'] = [float(self.center[0]), float(self.center[1])]
        if self.label is not None:
            out['label'] = str(self.label)
        return out


@dataclass
class GestureEvent:
    """一次表情/手势识别结果（表情手势同学的模型输出这个）。

    label       识别出的类别名，例如 'smile'、'wave'、'thumbs_up'。由你们自己定义，
                再在 run_gesture_dance.py 的 --map 里映射到跳舞动作。
    confidence  0～1。
    """
    label: str
    confidence: float = 1.0


@dataclass
class Frame:
    """开发板冻结的一帧：图像 + 拍摄瞬间的六关节真实读数。"""
    frame_id: str
    jpeg: bytes
    image_size: Tuple[int, int]
    joints: List[dict] = field(default_factory=list)
    captured_at: int = 0

    @property
    def image(self):
        """BGR numpy 图像（需要 opencv-python）。"""
        import cv2
        import numpy as np
        return cv2.imdecode(np.frombuffer(self.jpeg, np.uint8), cv2.IMREAD_COLOR)
