"""Check evaluation gates that protect an independent test from data leakage."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from waste_vision.model_effect_eval import audit_independent_test, match_detections, summarize_ms
from waste_vision.trial20 import CLASSES


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IndependentTestAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.classes = self.root / "classes.txt"
        self.classes.write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
        self.manifest_path = self.root / "manifest.json"
        self.rows = {}
        for index, split in enumerate(("train", "val", "test")):
            image_dir = self.root / "images" / split
            label_dir = self.root / "labels" / split
            image_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            image = image_dir / f"{split}.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") * 2 + bytes([index]))
            label = label_dir / f"{split}.txt"
            label.write_text(f"{index} 0.5 0.5 0.4 0.4\n", encoding="utf-8")
            self.rows[split] = [{"file": image.name, "capture_group": split + "_day",
                                "image_sha256": sha(image), "label_sha256": sha(label),
                                "scene_tags": ["single"], "empty_verified": False}]
        self.write_manifest()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps({"purpose": "independent_test", "sealed": True,
                                                  "classes": CLASSES, "splits": self.rows}), encoding="utf-8")

    def test_audit_passes_but_reports_missing_coverage(self):
        result = audit_independent_test(self.root, self.manifest_path, self.classes)
        self.assertEqual(result["splits"]["test"]["images"], 1)
        self.assertIn("block_blue", result["missing_test_classes"])
        self.assertIn("empty", result["missing_test_scenes"])

    def test_cross_split_capture_group_is_rejected(self):
        self.rows["test"][0]["capture_group"] = "train_day"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "crosses"):
            audit_independent_test(self.root, self.manifest_path, self.classes)

    def test_unverified_empty_label_is_rejected(self):
        label = self.root / "labels" / "test" / "test.txt"
        label.write_text("", encoding="utf-8")
        self.rows["test"][0]["label_sha256"] = sha(label)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "explicit human verification"):
            audit_independent_test(self.root, self.manifest_path, self.classes)

    def test_exact_duplicate_across_groups_is_rejected(self):
        train_image = self.root / "images" / "train" / "train.png"
        test_image = self.root / "images" / "test" / "test.png"
        test_image.write_bytes(train_image.read_bytes())
        self.rows["test"][0]["image_sha256"] = sha(test_image)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "exact duplicate"):
            audit_independent_test(self.root, self.manifest_path, self.classes)

    def test_single_tag_must_have_one_human_box(self):
        label = self.root / "labels" / "test" / "test.txt"
        label.write_text("2 0.5 0.5 0.4 0.4\n2 0.2 0.2 0.1 0.1\n", encoding="utf-8")
        self.rows["test"][0]["label_sha256"] = sha(label)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "single/multi"):
            audit_independent_test(self.root, self.manifest_path, self.classes)

    def test_trial_flow_cannot_be_named_independent_test(self):
        self.write_manifest()
        content = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        content["purpose"] = "training pipeline smoke run ONLY"
        self.manifest_path.write_text(json.dumps(content), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sealed independent_test"):
            audit_independent_test(self.root, self.manifest_path, self.classes)

    def test_locked_threshold_matching_flags_duplicate_wrong_class_and_offset(self):
        truth = [{"class_id": 0, "xyxy": [10, 10, 50, 50]},
                 {"class_id": 1, "xyxy": [60, 10, 90, 40]}]
        predictions = [
            {"raw_label": "block_blue", "confidence": 0.9, "bbox_xyxy_raw_camera": [11, 11, 49, 49]},
            {"raw_label": "block_blue", "confidence": 0.8, "bbox_xyxy_raw_camera": [10, 10, 50, 50]},
            {"raw_label": "block_red", "confidence": 0.7, "bbox_xyxy_raw_camera": [60, 10, 90, 40]},
            {"raw_label": "block_green", "confidence": 0.6, "bbox_xyxy_raw_camera": [0, 0, 20, 20]},
        ]
        result = match_detections(predictions, truth)
        self.assertEqual(result["block_blue"], {"tp": 1, "fp": 1, "fn": 0})
        self.assertEqual(result["block_green"], {"tp": 0, "fp": 1, "fn": 1})
        self.assertEqual(result["block_red"], {"tp": 0, "fp": 1, "fn": 0})

    def test_nearest_rank_p95(self):
        self.assertEqual(summarize_ms([1, 2, 3, 4, 5])["p95_ms"], 5)


if __name__ == "__main__":
    unittest.main()
