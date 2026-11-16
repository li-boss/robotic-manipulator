"""文字命令模型的训练、加载和拒绝行为；不连接机械臂。"""

from pathlib import Path
import tempfile
import unittest

from pc.voice_control.text_classification.classifier import IntentClassifier
from pc.voice_control.text_classification.dataset import check_disjoint, load_examples
from pc.voice_control.text_classification.train import DATA_DIR, augment_negatives, train


class TextClassificationTests(unittest.TestCase):
    def test_training_data_and_generated_negatives_do_not_overlap_test(self):
        training = load_examples(DATA_DIR / "train.jsonl")
        validation = load_examples(DATA_DIR / "val.jsonl")
        testing = load_examples(DATA_DIR / "test.jsonl")
        check_disjoint([*training, *augment_negatives(training, validation)], validation, testing)

    def test_train_load_predict_and_reject(self):
        with tempfile.TemporaryDirectory() as folder:
            model_path = Path(folder) / "classifier.joblib"
            train(DATA_DIR / "train.jsonl", DATA_DIR / "val.jsonl", model_path)
            classifier = IntentClassifier(model_path)
            self.assertEqual(classifier.predict("机械臂回直立位").action, "POSE_VERTICAL")
            self.assertIsNone(classifier.predict("暂时别跳音乐那支舞").action)
            self.assertIsNone(classifier.predict("").action)


if __name__ == "__main__":
    unittest.main()
