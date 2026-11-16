"""评估文字命令分类器：python -m pc.voice_control.text_classification.evaluate。"""

import argparse
from pathlib import Path

from sklearn.metrics import classification_report

from .classifier import DEFAULT_MODEL_PATH, NO_COMMAND, IntentClassifier
from .commands import load_commands
from .dataset import check_disjoint, load_examples
from .train import DATA_DIR, augment_negatives


def evaluate(model_path: Path, data_path: Path, train_path: Path, val_path: Path) -> None:
    training = load_examples(train_path)
    validation = load_examples(val_path)
    testing = load_examples(data_path)
    check_disjoint(training, validation, testing)
    check_disjoint([*training, *augment_negatives(training, validation)], validation, testing)
    classifier = IntentClassifier(model_path)
    expected = [action for _, action in testing]
    predicted = [classifier.predict(text).action or NO_COMMAND for text, _ in testing]
    labels = [*load_commands(), NO_COMMAND]
    print(classification_report(expected, predicted, labels=labels, zero_division=0))
    false_actions = sum(truth == NO_COMMAND and result != NO_COMMAND
                        for truth, result in zip(expected, predicted))
    wrong_actions = sum(truth != NO_COMMAND and result not in (NO_COMMAND, truth)
                        for truth, result in zip(expected, predicted))
    print(f"无命令误触发：{false_actions}/{expected.count(NO_COMMAND)}")
    print(f"命令错触发：{wrong_actions}/{len(expected) - expected.count(NO_COMMAND)}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--data", type=Path, default=DATA_DIR / "test.jsonl")
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train.jsonl")
    parser.add_argument("--val", type=Path, default=DATA_DIR / "val.jsonl")
    args = parser.parse_args(argv)
    evaluate(args.model, args.data, args.train, args.val)


if __name__ == "__main__":
    main()
