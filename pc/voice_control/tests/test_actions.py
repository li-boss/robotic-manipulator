"""用假客户端验证命令分发与会话检查，不连接开发板。"""

import unittest
from unittest.mock import Mock, patch
import hashlib
import io
import json

from pc.armlink import BoardError
from pc.voice_control.actions import HANDLERS, _detect_blocks, execute
from pc.voice_control.pipeline import run_once
from pc.voice_control.text_classification.classifier import ActionPrediction
from pc.voice_control.text_classification.commands import NO_COMMAND, load_commands
from pc.armlink.types import BlockDetection


class ActionDispatchTests(unittest.TestCase):
    def test_every_action_has_a_handler(self):
        self.assertEqual(set(HANDLERS), set(load_commands()) - {NO_COMMAND})

    def test_unimplemented_command_never_contacts_board(self):
        client = Mock()
        messages = []
        self.assertFalse(execute("POSE_VERTICAL", client, "竖直", messages.append))
        client.assert_not_called()
        self.assertFalse(client.method_calls)
        self.assertIn("尚未接入", messages[0])

    def test_block_grasp_requires_session_before_inference(self):
        client = Mock()
        client.mode.return_value = {"armed": False}
        with patch("pc.voice_control.actions._detect_blocks") as detect:
            with self.assertRaises(BoardError):
                execute("START_BLOCK_GRASP", client, "开始识别夹取", lambda _: None)
            detect.assert_not_called()
        client.freeze_frame.assert_not_called()
        client.submit_block.assert_not_called()

    def test_block_grasp_submits_one_matching_candidate_from_frozen_frame(self):
        client = Mock()
        client.mode.return_value = {"armed": True, "kind": "block_grasp", "busy": False,
                                    "session_id": "session", "min_score": 0.5}
        client.freeze_frame.return_value.frame_id = "frame"
        red = BlockDetection(corners=[[0, 0], [1, 0], [1, 1], [0, 1]], score=0.7, label="block_red")
        yellow = BlockDetection(corners=[[0, 0], [1, 0], [1, 1], [0, 1]], score=0.95, label="block_yellow")
        client.submit_block.return_value = {"job": {"id": "job"}}
        client.wait_job.return_value = {"status": "completed"}
        with patch("pc.voice_control.actions._detect_blocks", return_value=[yellow, red]):
            self.assertTrue(execute("START_BLOCK_GRASP", client, "抓红色方块", lambda _: None))
        client.freeze_frame.assert_called_once_with("session")
        client.submit_block.assert_called_once_with("session", "frame", red)
        client.wait_job.assert_called_once_with("job")

    def test_block_grasp_does_not_submit_when_target_missing_or_session_changes(self):
        client = Mock()
        mode = {"armed": True, "kind": "block_grasp", "busy": False,
                "session_id": "session", "min_score": 0.5}
        client.mode.return_value = mode
        detections = [BlockDetection(corners=[[0, 0]] * 4, score=0.9, label="yellow")]
        with patch("pc.voice_control.actions._detect_blocks", return_value=detections):
            self.assertFalse(execute("START_BLOCK_GRASP", client, "抓红色方块", lambda _: None))
            client.mode.side_effect = [mode, dict(mode, session_id="new-session")]
            with self.assertRaises(BoardError):
                execute("START_BLOCK_GRASP", client, "开始识别夹取", lambda _: None)
        client.submit_block.assert_not_called()

    def test_block_grasp_rejects_spoken_destination(self):
        client = Mock()
        self.assertFalse(execute("START_BLOCK_GRASP", client, "把红色方块放到蓝色区域", lambda _: None))
        self.assertFalse(client.method_calls)

    def test_vision_response_must_match_frozen_jpeg(self):
        frame = Mock(jpeg=b"frozen jpeg")
        row = BlockDetection(corners=[[0, 0], [1, 0], [1, 1], [0, 1]],
                             score=0.8, label="block_red").to_dict()
        payload = {"image_sha256": hashlib.sha256(frame.jpeg).hexdigest(),
                   "detections": [row]}
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        response.__exit__ = Mock(return_value=False)
        with patch("pc.voice_control.actions.urlopen", return_value=response):
            self.assertEqual(_detect_blocks(frame)[0].label, "block_red")
        payload["image_sha256"] = "incorrect"
        response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        with patch("pc.voice_control.actions.urlopen", return_value=response):
            with self.assertRaises(BoardError):
                _detect_blocks(frame)

    def test_dance_requires_matching_armed_session(self):
        client = Mock()
        client.mode.return_value = {"armed": False}
        with self.assertRaises(BoardError):
            execute("MUSIC_DANCE_ROUTINE", client, "跳音乐舞蹈", lambda _: None)
        client.dance.assert_not_called()

    def test_pipeline_dispatches_authorized_dance_once(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = "跳课程舞蹈"
        classifier = Mock()
        classifier.predict.return_value = ActionPrediction("COURSE_DANCE_ROUTINE", 0.9)
        client = Mock()
        client.mode.return_value = {
            "armed": True, "kind": "gesture_dance", "routines": ["COURSE_DANCE_ROUTINE"],
            "busy": False, "session_id": "session",
        }
        client.dance.return_value = {"job": {"id": "job"}}
        client.wait_job.return_value = {"status": "completed"}
        self.assertTrue(run_once("sample.wav", recognizer, client, classifier=classifier, log=lambda _: None))
        client.dance.assert_called_once_with("session", "COURSE_DANCE_ROUTINE", label="跳课程舞蹈")
        client.wait_job.assert_called_once_with("job")


if __name__ == "__main__":
    unittest.main()
