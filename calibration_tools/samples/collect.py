"""A-only sample capture. It never commands the robot."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen


def collect(base_url: str, output: Path, sample_id: str, frame_id: str = "latest") -> Path:
    with urlopen(f"{base_url}/api/v1/frames/{frame_id}/meta", timeout=3) as response:
        metadata = json.load(response)
    actual_id = metadata["frame_id"]
    with urlopen(f"{base_url}/api/v1/frames/{actual_id}", timeout=3) as response:
        jpeg = response.read()
    output.mkdir(parents=True, exist_ok=True)
    image = output / f"{sample_id}_{actual_id}.jpg"
    if image.exists():
        raise FileExistsError(image)
    image.write_bytes(jpeg)
    record = {"sample_id": sample_id, "image": image.name, **metadata,
              "pixel_x": None, "pixel_y": None,
              "measured_x": None, "measured_y": None, "measured_unit": None,
              "camera_setup_version": None, "notes": "待测；实测点由B配合记录"}
    with (output / "samples.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return image


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("calibration_tools/samples/captures"))
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--frame-id", default="latest")
    args = parser.parse_args()
    print(collect(args.url, args.output, args.sample_id, args.frame_id))
