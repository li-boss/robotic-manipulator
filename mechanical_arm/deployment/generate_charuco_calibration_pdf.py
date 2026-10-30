"""Create a dimensioned vector calibration sheet. Pure file/image work; no hardware.

Run with reportlab, pypdf, pymupdf and OpenCV (aruco) installed.
The adjacent JSON pins the exact board definition for future calibration code.
"""
from pathlib import Path
import hashlib
import json

import cv2
import fitz
import numpy as np
from pypdf import PdfReader
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf"
QA = ROOT / "tmp" / "pdfs" / "charuco_v1"
PDF = OUT / "机械臂_ChArUco标定纸_A4_20mm_v1.pdf"
BOARD_ID = "MA-CHARUCO-9X6-20-14-V1"
PW, PH = 297.0, 210.0
BX, BY = 58.5, 45.0  # top-left of pattern, page mm
BW, BH = 180.0, 120.0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
    board = cv2.aruco.CharucoBoard((9, 6), 20.0, 14.0, dictionary)
    board.setLegacyPattern(False)
    assert len(board.getIds()) == 27
    assert len(board.getChessboardCorners()) == 40
    pdfmetrics.registerFont(TTFont("CN", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"))
    c = canvas.Canvas(str(PDF), pagesize=(PW * mm, PH * mm), pageCompression=1)
    c.setTitle("机械臂 ChArUco 标定纸 - A4横向 - 20毫米格长")
    c.setAuthor("MechanicalArm project")
    c.setSubject(BOARD_ID + "; DICT_4X4_100; legacyPattern=false; print at 100%")

    def text(x, y, value, size=9, align="left"):
        c.setFillColorRGB(0, 0, 0)
        c.setFont("CN", size)
        method = {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[align]
        method(x * mm, (PH - y) * mm, value)

    def line(x1, y1, x2, y2, width=0.5):
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(width)
        c.line(x1 * mm, (PH - y1) * mm, x2 * mm, (PH - y2) * mm)

    def rect(x, y, w, h):
        c.setFillColorRGB(0, 0, 0)
        c.rect(x * mm, (PH - y - h) * mm, w * mm, h * mm, stroke=0, fill=1)

    def arrow(x1, y1, x2, y2):
        line(x1, y1, x2, y2, 0.65)
        dx, dy = np.asarray([x2-x1, y2-y1], float)
        length = float(np.hypot(dx, dy))
        ux, uy = dx/length, dy/length
        for sign in (-1, 1):
            line(x2, y2, x2-2*ux+sign*uy, y2-2*uy-sign*ux, 0.65)

    text(148.5, 14, "机械臂相机标定板", 17, "center")
    text(148.5, 23, "ChArUco  |  A4 横向  |  100% 实际大小打印  |  关闭“适应页面”", 9.5, "center")
    text(BX, 36, "O (0, 0)", 8)
    arrow(BX+24, 34, BX+51, 34)
    text(BX+55, 35, "+X", 8)
    line(BX, 38.5, BX, BY-2, 0.45)
    arrow(BX-9, BY+10, BX-9, BY+38)
    text(BX-10, BY+45, "+Y", 8, "center")

    # Obtain black-square parity from the same exact OpenCV board definition.
    reference = board.generateImage((1800, 1200), marginSize=0, borderBits=1)
    for row in range(6):
        for col in range(9):
            if reference[row*200+10, col*200+10] == 0:
                rect(BX+col*20, BY+row*20, 20, 20)

    # Render each 4x4 marker + 1-module border as exact vector cells.
    # 14/6 mm modules remain exact; no image interpolation / raster resizing.
    marker_geometry = []
    for marker_id, points in zip(board.getIds().tolist(), board.getObjPoints()):
        x, y = map(float, points[0, :2])
        bits = cv2.aruco.generateImageMarker(dictionary, marker_id, 6, borderBits=1)
        # A single compound fill avoids hairline seams between adjacent modules.
        marker_path = c.beginPath()
        for row in range(6):
            for col in range(6):
                if bits[row, col] == 0:
                    px, py = BX+x+col*14/6, BY+y+row*14/6
                    marker_path.rect(px*mm, (PH-py-14/6)*mm, 14/6*mm, 14/6*mm)
        c.setFillColorRGB(0, 0, 0)
        c.drawPath(marker_path, stroke=0, fill=1, fillMode=1)
        marker_geometry.append({"id": marker_id, "corners_mm": points.tolist()})

    text(247, 65, "标定板参数", 10)
    for y, value in [
        (75, "棋盘：9 x 6 格"), (84, "格长：20 mm"),
        (93, "编码：14 mm"), (102, "内部角点：40 个"),
        (111, "编码编号：0 至 26"), (124, "DICT_4X4_100"),
        (133, "legacyPattern=false"), (146, "O：图案左上外角"),
        (155, "纸面坐标单位：mm"),
    ]:
        text(247, y, value, 8)

    # Pattern width dimension, outside the white detection margin.
    line(BX, 171.5, BX+BW, 171.5)
    for x in (BX, BX+BW):
        line(x, 169.5, x, 173.5)
    text(148.5, 177, "棋盘图案宽 180 mm，高 120 mm", 8, "center")

    # Independent 100 mm scales in both page directions.
    sx, sy = 98.5, 186.0
    line(sx, sy, sx+100, sy, 0.65)
    for n in range(0, 101, 10):
        tick = 2.5 if n in (0, 50, 100) else 1.5
        line(sx+n, sy-tick, sx+n, sy+tick)
    text(sx-4, sy+1, "0", 7.5, "right")
    text(sx+104, sy+1, "100 mm", 7.5)
    text(148.5, 194, "水平打印校验尺", 7.5, "center")
    vx, vy = 24.0, 58.0
    line(vx, vy, vx, vy+100, 0.65)
    for n in range(0, 101, 10):
        tick = 2.5 if n in (0, 50, 100) else 1.5
        line(vx-tick, vy+n, vx+tick, vy+n)
    text(vx, vy-4, "0", 7.5, "center")
    text(vx, vy+106, "100 mm", 7.5, "center")
    c.saveState()
    c.translate(12*mm, (PH-108)*mm)
    c.rotate(90)
    c.setFont("CN", 8)
    c.drawCentredString(0, 0, "竖直打印校验尺")
    c.restoreState()
    text(148.5, 201, "打印后用尺核对横、竖校验线均为100毫米；贴平整硬板，勿覆反光膜。", 8, "center")
    text(148.5, 204, BOARD_ID + "  |  OpenCV " + cv2.__version__, 6.5, "center")
    c.showPage()
    c.save()

    reader = PdfReader(str(PDF))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    # PDF decimal serialization rounds points; 0.0001 mm covers that rounding.
    assert abs(float(page.mediabox.width)/mm-PW) < 1e-4
    assert abs(float(page.mediabox.height)/mm-PH) < 1e-4
    assert "100%" in page.extract_text()
    document = fitz.open(PDF)
    pixmap = document[0].get_pixmap(matrix=fitz.Matrix(300/72, 300/72), alpha=False)
    pixmap.save(str(QA / "page_300dpi.png"))
    image = cv2.cvtColor(np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n), cv2.COLOR_RGB2GRAY)
    corners, ids, marker_corners, marker_ids = cv2.aruco.CharucoDetector(board).detectBoard(image)
    assert marker_ids is not None and set(marker_ids.ravel()) == set(range(27))
    assert ids is not None and set(ids.ravel()) == set(range(40))
    # Check the actual rendered corner positions against exact page coordinates.
    expected = (board.getChessboardCorners()[ids.ravel(), :2] + [BX, BY]) * (300/25.4)
    errors = np.linalg.norm(corners.reshape(-1, 2) - expected, axis=1)
    assert errors.max() < 2, errors.max()
    assert len(document[0].get_images()) == 0, "Board must remain vector-only"
    document[0].get_pixmap(matrix=fitz.Matrix(140/72, 140/72), alpha=False).save(str(QA / "preview.png"))
    manifest = {
        "board_id": BOARD_ID, "schema": "charuco_print_definition_v1",
        "opencv_version": cv2.__version__, "legacy_pattern": False,
        "dictionary": "DICT_4X4_100", "squares_x": 9, "squares_y": 6,
        "square_length_mm": 20.0, "marker_length_mm": 14.0, "marker_border_bits": 1,
        "page_mm": [PW, PH], "pattern_mm": [BW, BH],
        "pattern_top_left_on_page_mm": [BX, BY],
        "board_origin": "pattern top-left outer corner; X right; Y down in printed front view",
        "units": "mm", "marker_ids": board.getIds().tolist(),
        "marker_geometry": marker_geometry,
        "chessboard_corners_mm": board.getChessboardCorners().tolist(),
        "pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(),
        "verification": {"pages": 1, "vector_only": True, "render_dpi": 300,
                         "detected_markers": len(marker_ids), "detected_charuco_corners": len(ids),
                         "max_corner_error_px": float(errors.max()), "hardware_used": False},
    }
    (OUT / "charuco_9x6_20mm_v1.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"pdf": str(PDF), "verification": manifest["verification"], "sha256": manifest["pdf_sha256"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
