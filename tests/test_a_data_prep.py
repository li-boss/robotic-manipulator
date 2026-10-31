import tempfile
import unittest
from pathlib import Path

from waste_vision.label_check import check
from waste_vision.prepare_annotation import prepare


PNG_HEADER_100 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") * 2


class DataPrepTests(unittest.TestCase):
    def test_annotation_copy_is_traceable_and_non_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "one.png").write_bytes(PNG_HEADER_100)
            destination = Path(tmp) / "work"
            self.assertEqual(prepare(source, destination)["images"], 1)
            self.assertEqual((destination / "one.png").read_bytes(), PNG_HEADER_100)
            self.assertFalse(list(destination.glob("*.txt")))
            with self.assertRaises(FileExistsError):
                prepare(source, destination)

    def test_label_checker_rejects_outside_box_and_accepts_empty_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images, labels = root / "images", root / "labels"
            images.mkdir()
            labels.mkdir()
            (images / "object.png").write_bytes(PNG_HEADER_100)
            (images / "empty.png").write_bytes(PNG_HEADER_100)
            classes = root / "classes.txt"
            classes.write_text("block_blue\n", encoding="utf-8")
            (labels / "object.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")
            self.assertFalse(check(images, labels, classes)["ready_for_training"])
            (labels / "empty.txt").write_text("", encoding="utf-8")
            self.assertTrue(check(images, labels, classes)["ready_for_training"])
            (labels / "object.txt").write_text("0 0.9 0.5 0.4 0.4\n", encoding="utf-8")
            result = check(images, labels, classes)
            self.assertFalse(result["ready_for_training"])
            self.assertEqual(len(result["problems"]), 1)


if __name__ == "__main__":
    unittest.main()
