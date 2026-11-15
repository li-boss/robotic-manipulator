"""模型接入前的临时整句口令分类器。"""

from .classifier import ActionPrediction
from .commands import parse_command


class RuleClassifier:
    """模型接入前的临时实现：仅匹配 commands.json 中的完整口令。"""

    def predict(self, text: str) -> ActionPrediction:
        return ActionPrediction(action=parse_command(text))
