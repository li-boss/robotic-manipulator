"""本地STL正交视图，仅用于查看几何；不导入项目控制或相机模块。"""
from pathlib import Path
import struct

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def main():
    dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
    canvas = np.full((840, 1440, 3), 245, np.uint8)
    for row, link in enumerate(('link4', 'link5')):
        data = (ROOT / '三维结构文件' / (link + '.STL')).read_bytes()
        count = struct.unpack_from('<I', data, 80)[0]
        if len(data) != 84 + 50 * count:
            raise ValueError('STL长度无效')
        faces = np.frombuffer(data, dtype=dtype, count=count, offset=84)['vertices'].astype(float) * 1000
        for col, (axes, title) in enumerate((((0, 1, 2), 'XY / depth Z'), ((0, 2, 1), 'XZ / depth Y'), ((1, 2, 0), 'YZ / depth X'))):
            a, b, z = axes
            image = np.full((420, 480, 3), 245, np.uint8)
            lo = faces.min(axis=(0, 1)); hi = faces.max(axis=(0, 1))
            scale = min(390 / (hi[a] - lo[a]), 320 / (hi[b] - lo[b]))
            pixels = np.stack(((faces[:, :, a] - (hi[a] + lo[a]) / 2) * scale + 240,
                               -(faces[:, :, b] - (hi[b] + lo[b]) / 2) * scale + 235), axis=2)
            normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            normals /= np.where(lengths > 0, lengths, 1)[:, None]
            intensity = 65 + 160 * np.abs(normals[:, z])
            for index in np.argsort(faces[:, :, z].mean(axis=1)):
                v = int(intensity[index])
                cv2.fillConvexPoly(image, np.rint(pixels[index]).astype(np.int32), (v, int(v * .87), int(v * .73)))
            cv2.putText(image, link + ' ' + title, (15, 25), cv2.FONT_HERSHEY_SIMPLEX, .65, (40, 40, 40), 1, cv2.LINE_AA)
            cv2.putText(image, 'axes in mm / static geometry', (15, 402), cv2.FONT_HERSHEY_SIMPLEX, .5, (60, 60, 60), 1, cv2.LINE_AA)
            canvas[row * 420:(row + 1) * 420, col * 480:(col + 1) * 480] = image
    path = ROOT / 'implementation_backups/manipulation_20261016/tool_mesh_views.png'
    if not cv2.imwrite(str(path), canvas):
        raise RuntimeError('写图失败')
    print(path)


if __name__ == '__main__':
    main()
