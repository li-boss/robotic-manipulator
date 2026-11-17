"""训练文字命令分类器：python -m pc.voice_control.text_classification.train。"""

import argparse
import math
from pathlib import Path

import joblib
import sklearn

from .classifier import DEFAULT_MODEL_PATH, build_pipeline
from .commands import NO_COMMAND, load_commands
from .dataset import check_disjoint, load_examples


DATA_DIR = Path(__file__).with_name("data")
def choose_threshold(pipeline, examples: list[tuple[str, str]]) -> float:
    """用验证集的错误动作分数设拒绝线，避免接受已见到的误判。"""
    probabilities = pipeline.predict_proba([text for text, _ in examples])
    classes = pipeline.classes_
    unsafe_scores = []
    for (_, expected), row in zip(examples, probabilities):
        index = int(row.argmax())
        predicted = classes[index]
        if predicted != NO_COMMAND and predicted != expected:
            unsafe_scores.append(float(row[index]))
    highest_unsafe = max(unsafe_scores, default=0.0)
    if highest_unsafe >= 1.0:
        raise ValueError("验证集出现置信度为 1 的错误动作，无法设置安全拒绝阈值")
    return float(max(0.5, math.nextafter(highest_unsafe, 1.0)))


def train(train_path: Path, val_path: Path, output_path: Path) -> None:
    training = load_examples(train_path)
    validation = load_examples(val_path)
    check_disjoint(training, validation)
    expected = set(load_commands())
    actual = {action for _, action in training}
    if actual != expected:
        raise ValueError(f"训练集类别不完整：缺少 {sorted(expected - actual)}，额外 {sorted(actual - expected)}")
    validation_labels = {action for _, action in validation}
    if validation_labels != expected:
        raise ValueError(f"验证集类别不完整：缺少 {sorted(expected - validation_labels)}")

    pipeline = build_pipeline()
    pipeline.fit([text for text, _ in training], [action for _, action in training])
    threshold = choose_threshold(pipeline, validation)
    probabilities = pipeline.predict_proba([text for text, _ in validation])
    accepted = {action: 0 for action in load_commands() if action != NO_COMMAND}
    for (_, expected_action), row in zip(validation, probabilities):
        index = int(row.argmax())
        action = pipeline.classes_[index]
        if action != NO_COMMAND and float(row[index]) >= threshold:
            if action != expected_action:
                raise ValueError("验证集仍有命令误触发，拒绝保存模型")
            accepted[action] += 1
    missing = [action for action, count in accepted.items() if count == 0]
    if missing:
        raise ValueError(f"验证集中没有成功识别的命令类别：{missing}；拒绝保存模型")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(format=1, pipeline=pipeline, threshold=threshold,
                     actions=sorted(load_commands()), sklearn_version=sklearn.__version__), output_path)
    print(f"已训练：{len(training)} 条显式标注样本；验证：{len(validation)} 条")
    print(f"验证集正确接受命令：{sum(accepted.values())}/{sum(action != NO_COMMAND for _, action in validation)}；命令误触发：0")
    print(f"拒绝阈值：{threshold:.4f}；模型：{output_path}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train.jsonl")
    parser.add_argument("--val", type=Path, default=DATA_DIR / "val.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_MODEL_PATH)
    args = parser.parse_args(argv)
    train(args.train, args.val, args.output)


if __name__ == "__main__":
    main()
