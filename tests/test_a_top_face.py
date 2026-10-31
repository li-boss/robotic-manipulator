"""Geometry and failure checks for optional top-face localization."""
import unittest
import json
import tempfile
from pathlib import Path
from time import sleep

import cv2
import numpy as np

from camera_service.camera import FrameError, FrameStore
from camera_service.service import VisionService
from waste_vision.detector import Detector
from waste_vision.top_face import TopFaceLocator, diagonal_intersection


def blue_bgr():
    return tuple(int(v) for v in cv2.cvtColor(
        np.uint8([[[103, 230, 225]]]), cv2.COLOR_HSV2BGR)[0, 0])


def jpeg_with_polygons(polygons):
    image = np.full((480, 640, 3), 220, dtype=np.uint8)
    for polygon in polygons:
        cv2.fillConvexPoly(image, np.asarray(polygon, dtype=np.int32), blue_bgr())
    ok, data = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return data.tobytes()


def target(box):
    return {"raw_label": "block_blue", "bbox": list(box), "confidence": 0.9,
            "center_x": (box[0] + box[2]) / 2, "center_y": (box[1] + box[3]) / 2}


class TopFaceTests(unittest.TestCase):
    def setUp(self):
        self.locator = TopFaceLocator("configs/a_top_face_hsv.json")

    def test_perspective_intersection_and_local_to_raw_offset(self):
        polygon = [[150, 120], [350, 130], [325, 260], [130, 230]]
        raw = jpeg_with_polygons([polygon])
        face = self.locator.locate(raw, 640, 480, [target((100, 80, 380, 360))])[0][0]
        reference = diagonal_intersection(polygon)
        self.assertEqual(face["status"], "candidate")
        self.assertLess(np.hypot(face["candidate_point"]["x"] - reference[0],
                                 face["candidate_point"]["y"] - reference[1]), 3.5)
        moved = [[x + 80, y + 30] for x, y in polygon]
        moved_face = self.locator.locate(
            jpeg_with_polygons([moved]), 640, 480,
            [target((180, 110, 460, 390))])[0][0]
        self.assertEqual(moved_face["status"], "candidate")
        self.assertAlmostEqual(moved_face["candidate_point"]["x"] -
                               face["candidate_point"]["x"], 80, delta=2)
        self.assertAlmostEqual(moved_face["candidate_point"]["y"] -
                               face["candidate_point"]["y"], 30, delta=2)

    def test_empty_color_triangle_and_two_similar_regions_require_review(self):
        white = jpeg_with_polygons([])
        no_color = self.locator.locate(white, 640, 480,
                                       [target((100, 80, 380, 360))])[0][0]
        self.assertEqual(no_color["status"], "review")
        self.assertIsNone(no_color["candidate_point"])
        smaller = np.full((240, 320, 3), 220, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", smaller)
        self.assertTrue(ok)
        mismatch = self.locator.locate(encoded.tobytes(), 320, 240,
                                       [target((20, 20, 250, 220))])[0][0]
        self.assertEqual(mismatch["reason"], "image_size_mismatch")
        triangle = jpeg_with_polygons([[[140, 120], [340, 150], [210, 290]]])
        bad_shape = self.locator.locate(triangle, 640, 480,
                                        [target((100, 80, 380, 360))])[0][0]
        self.assertEqual(bad_shape["status"], "review")
        self.assertIsNone(bad_shape["candidate_point"])
        adjacent = jpeg_with_polygons([
            [[120, 130], [230, 130], [230, 240], [120, 240]],
            [[270, 130], [380, 130], [380, 240], [270, 240]],
        ])
        ambiguous = self.locator.locate(adjacent, 640, 480,
                                         [target((100, 80, 400, 320))])[0][0]
        self.assertEqual(ambiguous["reason"], "ambiguous_color_regions")
        self.assertIsNone(ambiguous["candidate_point"])
        touching = jpeg_with_polygons([
            [[120, 130], [240, 130], [240, 230], [120, 230]],
            [[240, 130], [360, 130], [360, 230], [240, 230]],
        ])
        merged = self.locator.locate(touching, 640, 480,
                                     [target((100, 80, 380, 320))])[0][0]
        self.assertEqual(merged["reason"], "top_shape_too_elongated")
        self.assertIsNone(merged["candidate_point"])
        shared = jpeg_with_polygons([[[200, 120], [300, 120], [300, 220], [200, 220]]])
        duplicate = self.locator.locate(shared, 640, 480,
                                        [target((150, 80, 360, 320)),
                                         target((160, 80, 370, 320))])[0]
        self.assertTrue(all(face["reason"] == "same_color_target_assignment_ambiguous"
                            and face["candidate_point"] is None for face in duplicate))

    def test_frame_boundary_and_old_api_box_center_remain_separate(self):
        polygon = [[170, 120], [350, 120], [350, 260], [170, 260]]
        raw = jpeg_with_polygons([polygon])
        at_boundary = self.locator.locate(raw, 640, 480,
                                           [target((100, 80, 420, 480))])[0][0]
        self.assertEqual(at_boundary["reason"], "frame_boundary")
        self.assertIsNone(at_boundary["candidate_point"])

        class Backend:
            is_simulated = False
            box_coordinate_space = "raw_camera"

            def infer(self, frame):
                return [{"label": "block_blue", "confidence": 0.9,
                         "bbox": [100, 80, 380, 360]}]

        frame = FrameStore("test", max_age_ms=0).publish(raw, 640, 480)
        old = Detector(Backend(), "configs/a_category_mapping.json").detect(frame)
        new = Detector(Backend(), "configs/a_category_mapping.json",
                       top_face_locator=self.locator).detect(frame)
        self.assertNotIn("top_face", old["targets"][0])
        self.assertEqual((new["targets"][0]["center_x"], new["targets"][0]["center_y"]),
                         (240.0, 220.0))
        self.assertEqual(new["targets"][0]["target_status"], "unmapped")
        self.assertEqual(new["targets"][0]["top_face"]["status"], "candidate")
        self.assertNotEqual(new["targets"][0]["top_face"]["candidate_point"]["y"], 220.0)

    def test_tight_box_expansion_and_roi_boundary_are_distinct(self):
        polygon = [[150, 120], [350, 130], [325, 260], [130, 230]]
        raw = jpeg_with_polygons([polygon])
        tight = target((135, 115, 340, 350))
        expanded = self.locator.locate(raw, 640, 480, [tight])[0][0]
        self.assertEqual(expanded["status"], "candidate")
        self.assertTrue(expanded["quality"]["touches_original_box"])
        self.assertGreater(expanded["quality"]["original_box_overlap_fraction"], .65)
        config = json.loads(Path("configs/a_top_face_hsv.json").read_text(encoding="utf-8"))
        config["roi_expand_fraction"] = 0
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "strict.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            strict = TopFaceLocator(path).locate(raw, 640, 480, [tight])[0][0]
        self.assertEqual(strict["reason"], "color_region_clipped_by_roi")
        self.assertIsNone(strict["candidate_point"])

    def test_invalid_config_fails_and_merged_or_neighbor_region_reviews(self):
        config = json.loads(Path("configs/a_top_face_hsv.json").read_text(encoding="utf-8"))
        config["classes"]["block_red"]["hue_ranges"] = [[170, 190]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                TopFaceLocator(path)
        polygons = [[[120, 120], [210, 120], [210, 220], [120, 220]],
                    [[225, 120], [315, 120], [315, 220], [225, 220]]]
        raw = jpeg_with_polygons(polygons)
        ambiguous = self.locator.locate(raw, 640, 480,
                                        [target((100, 80, 330, 300))])[0][0]
        self.assertEqual(ambiguous["status"], "review")
        self.assertEqual(ambiguous["reason"], "ambiguous_color_regions")

    def test_visible_top_with_large_missing_corner_is_not_completed(self):
        image = np.full((480, 640, 3), 220, dtype=np.uint8)
        concave = np.asarray([[130, 120], [350, 120], [350, 250],
                              [280, 250], [280, 170], [130, 170]], dtype=np.int32)
        cv2.fillPoly(image, [concave], blue_bgr())
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        face = self.locator.locate(encoded.tobytes(), 640, 480,
                                   [target((100, 80, 390, 350))])[0][0]
        self.assertEqual(face["status"], "review")
        self.assertIsNone(face["candidate_point"])
        self.assertIn(face["reason"], {"nonconvex_or_occluded_color_region",
                                        "quad_unreliable", "quad_boundary_mismatch"})

    def test_frame_expiring_during_top_face_is_not_returned_or_refreshed_on_retry(self):
        class Backend:
            is_simulated = False
            box_coordinate_space = "raw_camera"

            def infer(self, _frame):
                return [{"label": "block_blue", "confidence": .9,
                         "bbox": [100, 80, 380, 360]}]

        class SlowLocator:
            def locate(self, jpeg, width, height, targets):
                sleep(.05)
                return self_locator.locate(jpeg, width, height, targets)

        self_locator = self.locator
        store = FrameStore("live", max_age_ms=20)
        store.publish(jpeg_with_polygons([[[150, 120], [350, 130], [325, 260], [130, 230]]]),
                      640, 480)
        service = VisionService(store, Detector(Backend(), "configs/a_category_mapping.json",
                                                top_face_locator=SlowLocator()))
        with self.assertRaises(FrameError) as caught:
            service.detect("slow-top-face", "latest")
        self.assertEqual(caught.exception.code, "FRAME_EXPIRED")
        with self.assertRaises(FrameError) as retry:
            service.detect("slow-top-face", "latest")
        self.assertIs(retry.exception, caught.exception)


if __name__ == "__main__":
    unittest.main()
