"""语音控制会话中的八类命令处理。"""

import time
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from armlink import BoardError
from block_vision.run_block_grasp import attempt as grasp_attempt
from gesture.run_gesture_dance import Debouncer
from lan.remote_recognizer import RemoteProtocolError, RemoteRecognizeError

KIND = "voice_control"
EXPRESSION_LABELS = {"smile"}
PLACE_WORDS = ("放到", "放进", "放入", "投放到")
COLORS = {"红": "red", "黄": "yellow", "蓝": "blue", "绿": "green"}

_DIGITS = {
    "零": 0,
    "〇": 0,
    "幺": 1,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_NUMBER = r"[0-9零〇幺一二两三四五六七八九十百]+"
# “N号”“第N”“N舵机/关节”任一写法都算提到一个舵机，用于发现一句话里有多个舵机。
_JOINT = re.compile(
    r"第\s*(" + _NUMBER + r")|(" + _NUMBER + r")\s*(?:号|舵机|关节|电机)"
)
_ANGLE = re.compile(
    r"(" + _NUMBER + r")\s*(?:[点.．]\s*[0-9零一二三四五六七八九]+\s*)?度"
)
_DECIMAL = re.compile(_NUMBER + r"\s*[点.．]\s*[0-9零一二三四五六七八九]+\s*度")


def chinese_number(text):
    """'135' / '一百三十五' / '一三五' / '九十' / '十五' / '两百' → int；无法解析时抛 ValueError。"""
    if text.isdigit():
        return int(text)
    if any(c not in _DIGITS and c not in "十百" for c in text) or not text:
        raise ValueError(f"无法识别的数字：{text}")
    if "十" not in text and "百" not in text:  # 逐位读法：一三五
        return int("".join(str(_DIGITS[c]) for c in text))
    total, rest = 0, text
    if "百" in rest:
        head, _, rest = rest.partition("百")
        if len(head) != 1 or head not in _DIGITS:
            raise ValueError(f"无法识别的数字：{text}")
        total += _DIGITS[head] * 100
        if len(rest) == 1 and rest in _DIGITS and rest != "零":  # 一百五 = 150
            return total + _DIGITS[rest] * 10
        rest = rest.lstrip("零〇")
    if "十" in rest:
        head, _, tail = rest.partition("十")
        if len(head) > 1 or (head and head not in _DIGITS):
            raise ValueError(f"无法识别的数字：{text}")
        total += (_DIGITS[head] if head else 1) * 10
        rest = tail
    if rest:
        if len(rest) != 1 or rest not in _DIGITS:
            raise ValueError(f"无法识别的数字：{text}")
        total += _DIGITS[rest]
    return total


def parse_servo_command(text):
    """返回 (舵机编号, 目标角度)；都必须恰好出现一次。"""
    if not isinstance(text, str):
        raise ValueError("没有识别文字")
    compact = re.sub(r"\s+", "", text)
    if _DECIMAL.search(compact):
        raise ValueError("角度必须是整数，不接受小数")
    joints = {chinese_number(a or b) for a, b in _JOINT.findall(compact)}
    if len(joints) != 1:
        raise ValueError(
            "没听清是几号舵机"
            if not joints
            else "一句话里出现了多个舵机，请一次只说一个"
        )
    angles = {chinese_number(m) for m in _ANGLE.findall(compact)}
    if len(angles) != 1:
        raise ValueError(
            "没听清要转到多少度" if not angles else "一句话里出现了多个角度，请只说一个"
        )
    joint, angle = joints.pop(), angles.pop()
    if not 1 <= joint <= 6:
        raise ValueError(f"舵机编号须为 1～6，听到的是 {joint}")
    return joint, angle


class StopProgram(Exception):
    """识别服务回复不合协议等：停止本程序（不触发任何动作）。"""


def requested_color(text):
    """说了一种颜色只抓该颜色，说了多种不提交。"""
    colors = {
        name
        for word, name in COLORS.items()
        if f"{word}色" in text or f"{word}方块" in text or f"{word}积木" in text
    }
    if len(colors) > 1:
        raise ValueError("语音中出现多种颜色，无法确定要抓的方块")
    return next(iter(colors), None)


class ColorFilter:
    """只保留指定颜色的候选；标签写法兼容 yellow 与 block_yellow。"""

    def __init__(self, detector, color):
        self.detector, self.color = detector, color
        if hasattr(detector, "detect_jpeg"):
            self.detect_jpeg = lambda *a: self._keep(detector.detect_jpeg(*a))

    def _keep(self, found):
        return [d for d in found if d.label in (self.color, f"block_{self.color}")]

    def detect(self, image):
        return self._keep(self.detector.detect(image))


class VoiceMode:
    def __init__(
        self,
        client,
        log=print,
        detector=None,
        recognizer=None,
        source=None,
        mapping=None,
        recog_seconds=20.0,
        stable=3,
        threshold=0.2,
    ):
        self.client, self.log = client, log
        self.detector, self.recognizer, self.source = detector, recognizer, source
        self.mapping = mapping or {}
        self.recog_seconds, self.stable, self.threshold = (
            recog_seconds,
            stable,
            threshold,
        )
        self.session = None
        self.handlers = {
            "MUSIC_DANCE_ROUTINE": lambda text: self.dance("MUSIC_DANCE_ROUTINE", text),
            "COURSE_DANCE_ROUTINE": lambda text: self.dance(
                "COURSE_DANCE_ROUTINE", text
            ),
            "POSE_VERTICAL": lambda text: self.pose("vertical", text),
            "POSE_OBSERVE": lambda text: self.pose("observe", text),
            "SET_SERVO_ABS_ANGLE": self.servo,
            "START_BLOCK_GRASP": self.grasp,
            "START_EXPR_RECOG": lambda text: self.recognize("expression", text),
            "START_GESTURE_RECOG": lambda text: self.recognize("gesture", text),
        }

    # ------------------------------------------------------------ 会话
    def current(self):
        """返回当前语音会话的 mode()；没有开启时返回 None。"""
        mode = self.client.mode()
        if not mode["armed"] or mode["kind"] != KIND:
            if self.session:
                self.log(
                    "语音会话已结束："
                    + str(mode.get("message") or "网页已关闭、次数用完或到期")
                )
            self.session = None
            return None
        if mode["session_id"] != self.session:
            self.session = mode["session_id"]
            grasp = mode.get("grasp")
            self.log(
                f"语音会话 {self.session[:8]}：最多 {mode['max_actions']} 次；竖直=“{mode['poses']['vertical']}”，"
                f"观察=“{mode['poses']['observe']}”；舵机用时 {mode['servo']['motion_ms']} ms；"
                f"跳舞 {mode['routines']}；抓方块 "
                + (f"{grasp['task_mode']}" if grasp else "未开启")
            )
        return mode

    def handle(self, action, text):
        """执行一条已分类的命令；返回 True 表示动作已执行完成。开发板拒绝只记录原因，不重试。"""
        mode = self.current()
        if mode is None:
            self.log(
                f"没有开启的语音会话，不执行“{text}”；请先在网页 /modes 开启语音控制"
            )
            return False
        if mode["busy"]:
            self.log("机械臂正在执行其他动作，本句不执行")
            return False
        try:
            return self.handlers[action](text)
        except BoardError as exc:
            self.log("开发板未执行：" + str(exc))
        except ValueError as exc:
            self.log("本句不执行：" + str(exc))
        return False

    def _finish(self, result, what):
        job = self.client.wait_job(result["job"]["id"])
        self.log(
            f"{what}：{job['status']} {job.get('message') or job.get('error') or ''}".rstrip()
        )
        return job["status"] == "completed"

    # ------------------------------------------------------------ 各命令
    def dance(self, routine, text):
        result = self.client.voice_dance(self.session, routine, text)
        self.log(f"跳 {routine}，约 {result['duration_ms'] / 1000:.1f} 秒")
        return self._finish(result, "跳舞结束")

    def pose(self, pose, text):
        result = self.client.voice_pose(self.session, pose, text)
        return self._finish(
            result, {"vertical": "回到竖直姿态", "observe": "回到观察姿态"}[pose]
        )

    def servo(self, text):
        joint, angle = parse_servo_command(text)
        self.log(f"{joint}号舵机 → {angle}°")
        result = self.client.voice_servo(self.session, joint, angle, text)
        return self._finish(result, f"{joint}号舵机")

    def grasp(self, text):
        if any(word in text for word in PLACE_WORDS):
            raise ValueError("语音指定放置位置尚未支持；放置点在网页开启会话时已选好")
        color = requested_color(text)
        if self.detector is None:
            raise ValueError(
                "没有可用的方块识别器（检查 pc/lan/lan_config.json 的 block_detect_url）"
            )
        mode = self.current()
        if mode is None or not mode.get("grasp"):
            raise ValueError("当前语音会话没有开启抓方块")
        detector = ColorFilter(self.detector, color) if color else self.detector
        request = self.client.voice_grasp(self.session, text)["request_id"]
        self.log("开始识别夹取" + (f"（只找{color}方块）" if color else ""))
        status = grasp_attempt(
            self.client,
            detector,
            self.session,
            request,
            mode["grasp"]["min_score"],
            self.log,
        )
        return status == "completed"

    def recognize(self, which, text):
        """开启一段表情/手势识别：识别确认后跳对应舞蹈一次，或到时结束。"""
        if self.recognizer is None or self.source is None:
            raise ValueError(
                "没有可用的表情手势识别器（检查 pc/lan/lan_config.json 的 gesture_url）"
            )
        name = "表情" if which == "expression" else "手势"
        wanted = {
            k: v
            for k, v in self.mapping.items()
            if (k in EXPRESSION_LABELS) == (which == "expression")
        }
        if not wanted:
            raise ValueError(f"--map 里没有{name}标签对应的舞蹈")
        self.log(
            f"开始{name}识别（最长 {self.recog_seconds:.0f} 秒）："
            + "，".join(f"{k}→{v}" for k, v in wanted.items())
        )
        debounce = Debouncer(self.stable, self.threshold, max_missed_frames=1)
        deadline = time.monotonic() + self.recog_seconds
        while time.monotonic() < deadline:
            image = self.source.read()
            try:
                detected = self.recognizer.recognize(image)
            except RemoteProtocolError as exc:
                raise StopProgram(
                    "识别服务回复不合协议，程序停止（不触发任何动作）：" + str(exc)
                ) from None
            except RemoteRecognizeError as exc:
                self.log("识别服务暂时不可用，本帧按“未识别”处理：" + str(exc))
                debounce.reset()
                time.sleep(1)
                continue
            if detected is not None and detected.label not in wanted:
                detected = None  # 本次只认{name}标签
            event = debounce.feed(detected)
            if event is None:
                continue
            self.log(
                f"{name}识别确认：{event.label}（{event.confidence:.2f}）→ {wanted[event.label]}"
            )
            result = self.client.voice_dance(
                self.session,
                wanted[event.label],
                text,
                trigger=which,
                label=event.label,
                confidence=event.confidence,
            )
            return self._finish(result, "跳舞结束")
        self.log(f"{name}识别结束：{self.recog_seconds:.0f} 秒内没有确认到约定的{name}")
        return False
