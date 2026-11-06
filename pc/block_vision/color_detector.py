"""示例检测器：HSV 颜色阈值 + 最小外接矩形。只用来跑通流程，正式请换成你们的模型。

它把颜色区域的最小外接矩形四角当作“顶面四角”，在俯视、方块侧面不太露出时可用；
斜视时侧面也是同色，四角会偏大——这正是需要真正模型的地方。
"""
import cv2
import numpy as np

from armlink import BlockDetection
from .base import BlockDetector

# OpenCV HSV：H 0～179，S/V 0～255
COLORS = {'yellow': ((20, 100, 100), (35, 255, 255)),
          'red': ((0, 120, 80), (8, 255, 255)),
          'blue': ((100, 120, 60), (125, 255, 255)),
          'green': ((45, 80, 60), (85, 255, 255))}


class ColorBlockDetector(BlockDetector):
    def __init__(self, colors=('yellow',), min_area=200):
        self.colors = colors
        self.min_area = min_area

    def detect(self, image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        found = []
        for name in self.colors:
            low, high = COLORS[name]
            mask = cv2.inRange(hsv, np.array(low), np.array(high))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < self.min_area:
                    continue
                rect = cv2.minAreaRect(contour)
                corners = cv2.boxPoints(rect)            # 4×2，沿轮廓顺序
                fill = area / max(rect[1][0] * rect[1][1], 1)   # 越接近1越像正方形色块
                found.append(BlockDetection(corners=corners.tolist(), score=float(min(1.0, fill)), label=name))
        return sorted(found, key=lambda d: d.score, reverse=True)
