import csv
import tempfile
import unittest
from pathlib import Path

from waste_vision.trial20 import pixels_to_yolo, read_manifest


class Trial20Tests(unittest.TestCase):
    def test_fixed_manifest_requires_exactly_20_unique_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["filename", "selection_note"])
                writer.writeheader()
                writer.writerows({"filename": f"{i}.jpg", "selection_note": ""} for i in range(20))
            self.assertEqual(len(read_manifest(path)), 20)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("0.jpg,duplicate\n")
            with self.assertRaises(ValueError):
                read_manifest(path)

    def test_pixel_box_conversion_and_bounds(self):
        self.assertEqual(pixels_to_yolo(3, 0, 0, 640, 480), "3 0.500000 0.500000 1.000000 1.000000")
        with self.assertRaises(ValueError):
            pixels_to_yolo(0, -1, 2, 20, 30)
        with self.assertRaises(ValueError):
            pixels_to_yolo(4, 1, 2, 20, 30)


if __name__ == "__main__":
    unittest.main()
