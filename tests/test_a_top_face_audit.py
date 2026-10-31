"""Risk checks for full-frame human audit matching and denominators."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.top_face_audit_eval import evaluate, match_boxes


class TopFaceAuditTests(unittest.TestCase):
    def test_matching_maximizes_count_before_iou(self):
        truth = [{"bbox": [0]}, {"bbox": [1]}]
        predictions = [{"bbox": [0]}, {"bbox": [1]}]
        scores = {(0, 0): .9, (0, 1): .8, (1, 0): .8, (1, 1): .1}
        with patch("tools.top_face_audit_eval.box_iou",
                   side_effect=lambda a, b: scores[(a[0], b[0])]):
            matches, missed, _, _ = match_boxes(truth, predictions, .5)
        self.assertEqual([(gi, pi) for gi, pi, _ in matches], [(0, 1), (1, 0)])
        self.assertEqual(missed, [])

    def test_matching_separates_miss_wrong_class_duplicate_and_background(self):
        truth = [{"bbox": [0, 0, 20, 20]}, {"bbox": [50, 50, 70, 70]}]
        predictions = [{"bbox": [0, 0, 20, 20]}, {"bbox": [1, 1, 19, 19]},
                       {"bbox": [100, 100, 120, 120]}]
        matched, missed, duplicate, background = match_boxes(truth, predictions, .5)
        self.assertEqual([(gi, pi) for gi, pi, _ in matched], [(0, 0)])
        self.assertEqual(missed, [1])
        self.assertEqual(duplicate, [1])
        self.assertEqual(background, [2])

    def test_no_complete_frames_is_unassessed_not_zero_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit.json"
            audit.write_text(json.dumps({"schema_version": "full-frame-human-audit-v1",
                                         "frames": {}}), encoding="utf-8")
            result = evaluate(root, audit, .5, None)
            self.assertEqual(result["status"], "未评估")
            self.assertIsNone(result["detection"])
            self.assertIsNone(result["overall"])

    def test_incomplete_candidate_is_policy_violation_not_coordinate_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = root / "case"
            case.mkdir()
            raw = b"saved raw frame"
            (case / "raw.jpg").write_bytes(raw)
            result = {"case_id": "batch__scene__frame", "frame_id": "f1",
                      "image_width": 640, "image_height": 480,
                      "targets": [{"bbox": [10, 10, 100, 100], "raw_label": "block_red",
                                   "top_face": {"status": "candidate", "candidate_point":
                                                {"x": 50, "y": 50}, "reason": None}}]}
            (case / "result.json").write_text(json.dumps(result), encoding="utf-8")
            audit = root / "audit.json"
            audit.write_text(json.dumps({"schema_version": "full-frame-human-audit-v1",
                                         "frames": {"batch__scene__frame": {
                                             "review_status": "complete",
                                             "source_sha256": hashlib.sha256(raw).hexdigest(),
                                             "frame_id": "f1", "image_width": 640,
                                             "image_height": 480,
                                             "objects": [{"bbox": [10, 10, 100, 100],
                                                          "raw_label": "block_blue",
                                                          "top_visibility": "incomplete",
                                                          "corners": None}]}}}), encoding="utf-8")
            metrics = evaluate(root, audit, .5, 10)
            self.assertEqual(metrics["detection"]["missed"], 0)
            self.assertEqual(metrics["detection"]["wrong_class"], 1)
            self.assertEqual(metrics["localization"]["policy_violation_candidate_on_incomplete"], 1)
            self.assertIsNone(metrics["localization"]["center_error_median_px"])
            self.assertEqual(metrics["overall"]["true_object_denominator"], 1)
            self.assertEqual(metrics["overall"]["correct_top_candidate"], 0)
            contents = json.loads(audit.read_text(encoding="utf-8"))
            contents["frames"]["batch__scene__frame"]["objects"][0]["top_visibility"] = "uncertain"
            audit.write_text(json.dumps(contents), encoding="utf-8")
            uncertain = evaluate(root, audit, .5, None)
            self.assertEqual(uncertain["localization"]["uncertain_true_tops_excluded_from_error"], 1)
            self.assertIsNone(uncertain["localization"]["center_error_median_px"])

    def test_empty_scene_and_unfinished_or_uncertain_do_not_invent_accuracy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = root / "empty"
            case.mkdir()
            raw = b"empty"
            (case / "raw.jpg").write_bytes(raw)
            (case / "result.json").write_text(json.dumps({
                "case_id": "empty", "frame_id": "e1", "image_width": 640,
                "image_height": 480, "targets": []}), encoding="utf-8")
            audit = root / "audit.json"
            entry = {"review_status": "complete", "source_sha256": hashlib.sha256(raw).hexdigest(),
                     "frame_id": "e1", "image_width": 640, "image_height": 480, "objects": []}
            audit.write_text(json.dumps({"schema_version": "full-frame-human-audit-v1",
                                         "frames": {"empty": entry}}), encoding="utf-8")
            result = evaluate(root, audit, .5, None)
            self.assertEqual(result["detection"]["background_false_positives"], 0)
            self.assertIsNone(result["overall"]["correct_top_candidate"])
            entry["review_status"] = "in_progress"
            audit.write_text(json.dumps({"schema_version": "full-frame-human-audit-v1",
                                         "frames": {"empty": entry}}), encoding="utf-8")
            self.assertEqual(evaluate(root, audit, .5, None)["status"], "未评估")


if __name__ == "__main__":
    unittest.main()
