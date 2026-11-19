"""交互式试用当前保存的命令文本分类模型。"""

import sys

from pc.voice_control.text_classification.classifier import IntentClassifier


def main() -> int:
    try:
        classifier = IntentClassifier()
    except (OSError, ValueError) as exc:
        print(f"模型加载失败：{exc}", file=sys.stderr)
        return 1

    print("已加载当前命令文本分类模型。直接回车退出。" + "\n")

    while True:
        try:
            text = input("输入命令文本：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            return 0

        prediction = classifier.predict(text)
        print(f"分类结果：{prediction.action}（接受阈值 {classifier.threshold:.3f}）")
        print(f"分数：{prediction.score:.3f}" + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
