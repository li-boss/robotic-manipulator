"""Experimental color-top localization on the exact raw JPEG used by detection.

YOLO boxes identify candidate blocks. A valid quadrilateral produces a pixel
candidate only; this module does not calculate robot coordinates or motion.
"""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from time import perf_counter

ALGORITHM_VERSION = "top-face-contour-roi-v3"


def diagonal_intersection(vertices: list[list[float]]) -> tuple[float, float]:
    """Intersection of diagonals 0-2 and 1-3 of a contour-ordered quadrilateral."""
    if len(vertices) != 4:
        raise ValueError("four vertices required")
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = vertices
    rx, ry = cx - ax, cy - ay
    sx, sy = dx - bx, dy - by
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-8:
        raise ValueError("degenerate quadrilateral diagonals")
    t = ((bx - ax) * sy - (by - ay) * sx) / denominator
    return ax + t * rx, ay + t * ry


class TopFaceLocator:
    """Conservative HSV/contour locator for one configured raw-camera view."""

    def __init__(self, config_path: str | Path):
        import cv2
        import numpy as np

        config_bytes = Path(config_path).read_bytes()
        config = json.loads(config_bytes.decode("utf-8"))
        if not isinstance(config.get("version"), str) or not config["version"].strip():
            raise ValueError("top-face config version is required")
        size = config.get("expected_image_size")
        if (not isinstance(size, list) or len(size) != 2 or
                any(type(v) is not int or v <= 0 for v in size)):
            raise ValueError("expected_image_size must be two positive integers")
        required = {"block_blue", "block_green", "block_red", "block_yellow"}
        if set(config.get("classes", {})) != required:
            raise ValueError("top-face config must define the four confirmed raw labels")
        for name, rule in config["classes"].items():
            if not rule.get("hue_ranges") or not all(
                    len(pair) == 2 and 0 <= pair[0] <= pair[1] <= 179
                    for pair in rule["hue_ranges"]):
                raise ValueError(f"invalid hue ranges for {name}")
            if not all(0 <= rule[key] <= 255 for key in ("saturation_min", "value_min")):
                raise ValueError(f"invalid saturation/value threshold for {name}")
        ranges = {"roi_expand_fraction": (0, .25),
                  "min_region_area_px": (1, 100000),
                  "min_region_fraction_of_box": (0, 1),
                  "max_second_component_ratio": (0, 1),
                  "min_region_box_margin_px": (0, 50),
                  "min_original_box_overlap": (0, 1),
                  "min_solidity": (0, 1), "max_quad_aspect_ratio": (1, 10),
                  "min_quad_iou": (0, 1), "min_quad_area_ratio": (0, 2),
                  "max_quad_area_ratio": (0, 2), "frame_border_margin_px": (0, 50)}
        for key, (low, high) in ranges.items():
            value = config.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid top-face config {key}: {value}")
        if config["min_quad_area_ratio"] > config["max_quad_area_ratio"]:
            raise ValueError("inverted quadrilateral area ratio range")
        self.cv2, self.np, self.config = cv2, np, config
        self.config_sha256 = hashlib.sha256(config_bytes).hexdigest()
        self.algorithm_version = ALGORITHM_VERSION
        self.version = config["version"]
        self._opening = np.ones((3, 3), dtype=np.uint8)
        self._closing = np.ones((5, 5), dtype=np.uint8)

    def locate(self, jpeg: bytes, width: int, height: int,
               targets: list[dict]) -> tuple[list[dict], float]:
        started = perf_counter()
        image = self.cv2.imdecode(self.np.frombuffer(jpeg, dtype=self.np.uint8),
                                  self.cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (height, width):
            raise ValueError("top-face source JPEG does not match detection frame")
        hsv = self.cv2.cvtColor(image, self.cv2.COLOR_BGR2HSV)
        results = [self._locate_one(hsv, width, height, target) for target in targets]
        # A same-colored patch must not silently become two usable target points
        # when overlapping YOLO boxes both claim it.
        for first in range(len(targets)):
            for second in range(first + 1, len(targets)):
                if targets[first].get("raw_label") != targets[second].get("raw_label"):
                    continue
                a, b = results[first], results[second]
                if a["status"] != "candidate" or b["status"] != "candidate":
                    continue
                qa = self.np.asarray(a["quad"], dtype=self.np.float32)
                qb = self.np.asarray(b["quad"], dtype=self.np.float32)
                overlap, _ = self.cv2.intersectConvexConvex(qa, qb)
                smaller = min(self.cv2.contourArea(qa), self.cv2.contourArea(qb))
                fraction = overlap / smaller if smaller > 0 else 0
                if fraction >= 0.25:
                    for item in (a, b):
                        item["status"] = "review"
                        item["reason"] = "same_color_target_assignment_ambiguous"
                        item["candidate_point"] = None
                        item["method"] = "visible_color_centroid_reference"
                        item["quality"]["same_color_quad_overlap_fraction"] = round(fraction, 4)
        return results, round((perf_counter() - started) * 1000, 3)

    def _mask(self, hsv_crop, label: str):
        cv2, np = self.cv2, self.np
        rule = self.config["classes"][label]
        mask = np.zeros(hsv_crop.shape[:2], dtype=np.uint8)
        for low_h, high_h in rule["hue_ranges"]:
            low = np.array([low_h, rule["saturation_min"], rule["value_min"]], dtype=np.uint8)
            high = np.array([high_h, 255, 255], dtype=np.uint8)
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv_crop, low, high))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._opening)
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._closing)

    def _locate_one(self, hsv, width: int, height: int, target: dict) -> dict:
        cv2, np = self.cv2, self.np
        result = {"status": "review", "reason": None, "coordinate_space": "raw_camera",
                  "method": None, "candidate_point": None, "visible_centroid": None,
                  "visible_contour": None, "quad": None, "quality": {
                      "threshold_version": self.version, "region_area_px": 0,
                      "component_count": 0, "touches_image_border": False,
                      "original_box_touches_image_border": False,
                      "roi_touches_image_border": False,
                      "color_region_touches_image_border": False,
                      "touches_original_box": False,
                      "quad_fitted": False}}

        def review(reason: str) -> dict:
            result["reason"] = reason
            if result["visible_centroid"] is not None:
                result["method"] = "visible_color_centroid_reference"
            return result

        expected = self.config.get("expected_image_size")
        if expected and [width, height] != expected:
            return review("image_size_mismatch")
        label = target.get("raw_label")
        if label not in self.config["classes"]:
            return review("unsupported_label")
        x1, y1, x2, y2 = (float(v) for v in target["bbox"])
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
            return review("invalid_box")
        if x2 <= x1 or y2 <= y1:
            return review("invalid_box")
        expand = float(self.config["roi_expand_fraction"])
        dx, dy = expand * (x2 - x1), expand * (y2 - y1)
        left, top = max(0, math.floor(x1 - dx)), max(0, math.floor(y1 - dy))
        right, bottom = min(width, math.ceil(x2 + dx)), min(height, math.ceil(y2 + dy))
        if right <= left or bottom <= top:
            return review("invalid_box")
        result["quality"]["roi_xyxy"] = [left, top, right, bottom]
        result["quality"]["original_box_xyxy"] = [x1, y1, x2, y2]
        crop = hsv[top:bottom, left:right]
        mask = self._mask(crop, label)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        area_min = max(float(self.config["min_region_area_px"]),
                       float(self.config["min_region_fraction_of_box"]) * (x2 - x1) * (y2 - y1))
        significant = [c for c in contours if cv2.contourArea(c) >= area_min]
        result["quality"]["component_count"] = len(significant)
        margin = int(self.config["frame_border_margin_px"])
        at_boundary = x1 <= margin or y1 <= margin or x2 >= width - margin or y2 >= height - margin
        result["quality"]["touches_image_border"] = at_boundary
        result["quality"]["original_box_touches_image_border"] = at_boundary
        result["quality"]["roi_touches_image_border"] = (
            left == 0 or top == 0 or right == width or bottom == height)
        if not contours:
            return review("no_color_region")
        if not significant:
            return review("small_color_region")
        contour = significant[0]
        area = cv2.contourArea(contour)
        result["quality"]["region_area_px"] = round(area, 1)
        result["quality"]["region_fraction_of_box"] = round(area / ((x2 - x1) * (y2 - y1)), 4)
        moments = cv2.moments(contour)
        if moments["m00"] <= 0:
            return review("invalid_color_contour")
        centroid_x = left + moments["m10"] / moments["m00"]
        centroid_y = top + moments["m01"] / moments["m00"]
        result["visible_centroid"] = {"x": round(centroid_x, 3), "y": round(centroid_y, 3)}
        perimeter = cv2.arcLength(contour, True)
        visible = cv2.approxPolyDP(contour, 0.008 * perimeter, True).reshape(-1, 2)
        result["visible_contour"] = [[int(x + left), int(y + top)] for x, y in visible]
        if len(significant) > 1:
            ratio = cv2.contourArea(significant[1]) / area
            result["quality"]["second_component_ratio"] = round(ratio, 4)
            if ratio >= float(self.config["max_second_component_ratio"]):
                return review("ambiguous_color_regions")
        region_x, region_y, region_w, region_h = cv2.boundingRect(contour)
        result["quality"]["color_region_touches_image_border"] = (
            left + region_x <= margin or top + region_y <= margin or
            left + region_x + region_w >= width - margin or
            top + region_y + region_h >= height - margin)
        if at_boundary:
            return review("frame_boundary")
        roi_margins = [region_x, region_y,
                       mask.shape[1] - region_x - region_w,
                       mask.shape[0] - region_y - region_h]
        result["quality"]["region_roi_margins_px"] = roi_margins
        result["quality"]["touches_original_box"] = bool(
            region_x + left <= x1 or region_y + top <= y1 or
            region_x + region_w + left >= x2 or region_y + region_h + top >= y2)
        if min(roi_margins) < int(self.config["min_region_box_margin_px"]):
            return review("color_region_clipped_by_roi")
        # The expanded ROI may include a neighboring patch. Require most of
        # the selected connected component to belong to the original target.
        component_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(component_mask, [contour], -1, 255, -1)
        original_mask = np.zeros(mask.shape, dtype=np.uint8)
        bx1, by1 = max(0, math.floor(x1) - left), max(0, math.floor(y1) - top)
        bx2, by2 = min(mask.shape[1], math.ceil(x2) - left), min(mask.shape[0], math.ceil(y2) - top)
        original_mask[by1:by2, bx1:bx2] = 255
        component_pixels = cv2.countNonZero(component_mask)
        overlap = cv2.countNonZero(cv2.bitwise_and(component_mask, original_mask))
        overlap_fraction = overlap / component_pixels if component_pixels else 0
        result["quality"]["original_box_overlap_fraction"] = round(overlap_fraction, 4)
        if overlap_fraction < float(self.config["min_original_box_overlap"]):
            return review("color_region_not_associated_with_original_box")
        relative_x = (centroid_x - x1) / (x2 - x1)
        relative_y = (centroid_y - y1) / (y2 - y1)
        result["quality"]["centroid_relative_to_box"] = [round(relative_x, 3), round(relative_y, 3)]
        if not (0.1 <= relative_x <= 0.9 and 0.05 <= relative_y <= 0.68):
            return review("color_region_outside_expected_top_zone")
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        solidity = area / hull_area if hull_area > 0 else 0
        result["quality"]["solidity"] = round(solidity, 4)
        if solidity < float(self.config["min_solidity"]):
            return review("nonconvex_or_occluded_color_region")
        quad_local = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(quad_local) != 4 or not cv2.isContourConvex(quad_local):
            return review("quad_unreliable")
        vertices = quad_local.reshape(4, 2).astype(float)
        edges = [float(np.linalg.norm(vertices[(i + 1) % 4] - vertices[i]))
                 for i in range(4)]
        axis_a = (edges[0] + edges[2]) / 2
        axis_b = (edges[1] + edges[3]) / 2
        aspect = max(axis_a, axis_b) / min(axis_a, axis_b) if min(axis_a, axis_b) > 0 else float("inf")
        result["quality"]["quad_aspect_ratio"] = round(aspect, 4)
        if aspect > float(self.config["max_quad_aspect_ratio"]):
            return review("top_shape_too_elongated")
        quad_area = cv2.contourArea(quad_local)
        area_ratio = quad_area / area if area > 0 else 0
        result["quality"]["quad_area_ratio"] = round(area_ratio, 4)
        if not (float(self.config["min_quad_area_ratio"]) <= area_ratio <=
                float(self.config["max_quad_area_ratio"])):
            return review("quad_boundary_mismatch")
        contour_mask = np.zeros(mask.shape, dtype=np.uint8)
        quad_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(contour_mask, [contour], -1, 255, -1)
        cv2.drawContours(quad_mask, [quad_local], -1, 255, -1)
        intersection = cv2.countNonZero(cv2.bitwise_and(contour_mask, quad_mask))
        union = cv2.countNonZero(cv2.bitwise_or(contour_mask, quad_mask))
        quad_iou = intersection / union if union else 0
        result["quality"]["quad_iou"] = round(quad_iou, 4)
        if quad_iou < float(self.config["min_quad_iou"]):
            return review("quad_boundary_mismatch")
        points = [[float(p[0][0] + left), float(p[0][1] + top)] for p in quad_local]
        try:
            center_x, center_y = diagonal_intersection(points)
        except ValueError:
            return review("degenerate_quad")
        if cv2.pointPolygonTest(quad_local, (center_x - left, center_y - top), False) < 0:
            return review("quad_center_outside_region")
        result["status"] = "candidate"
        result["reason"] = None
        result["method"] = "quadrilateral_diagonal_intersection"
        result["candidate_point"] = {"x": round(center_x, 3), "y": round(center_y, 3)}
        result["quad"] = [[round(x, 3), round(y, 3)] for x, y in points]
        result["quality"]["quad_fitted"] = True
        return result
