"""Isolated ArUco image worker. Never opens a camera or imports arm control."""
import base64
import json
import sys

import cv2
import numpy as np
from charuco_geometry import detect


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024:
            raise ValueError('图像请求过大')
        fields = json.loads(raw)
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(fields['jpeg_base64'], validate=True), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('原图解码失败')
        print(json.dumps(dict(result=detect(frame)), allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False))
        sys.exit(1)
