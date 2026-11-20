"""验证语音分类只向统一执行器分发一次；不连接开发板。"""
import unittest
from unittest.mock import Mock

from pc.voice_control.actions import VoiceMode
from pc.voice_control.pipeline import run_once
from pc.voice_control.text_classification.classifier import ActionPrediction
from pc.voice_control.text_classification.commands import NO_COMMAND, load_commands


class ActionDispatchTests(unittest.TestCase):
    def test_all_commands_are_handled(self):
        voice = VoiceMode(Mock())
        self.assertEqual(set(voice.handlers), set(load_commands()) - {NO_COMMAND})

    def test_pipeline_dispatches_once(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = '回到竖直姿态'
        classifier = Mock()
        classifier.predict.return_value = ActionPrediction('POSE_VERTICAL', .9)
        voice = Mock()
        voice.handle.return_value = True
        self.assertTrue(run_once('sample.wav', recognizer, Mock(), classifier=classifier,
                                 voice=voice, log=lambda _: None))
        voice.handle.assert_called_once_with('POSE_VERTICAL', '回到竖直姿态')

    def test_no_command_does_not_dispatch(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = '你好'
        classifier = Mock()
        classifier.predict.return_value = ActionPrediction(NO_COMMAND, .8)
        voice = Mock()
        self.assertFalse(run_once('sample.wav', recognizer, Mock(), classifier=classifier,
                                  voice=voice, log=lambda _: None))
        voice.handle.assert_not_called()

    def test_text_input_uses_same_dispatch_without_asr(self):
        classifier = Mock()
        classifier.predict.return_value = ActionPrediction('POSE_VERTICAL', .9)
        voice = Mock()
        voice.handle.return_value = True
        self.assertTrue(run_once('回到竖直姿态', None, Mock(), classifier=classifier,
                                 voice=voice, is_text=True, log=lambda _: None))
        classifier.predict.assert_called_once_with('回到竖直姿态')
        voice.handle.assert_called_once_with('POSE_VERTICAL', '回到竖直姿态')


if __name__ == '__main__':
    unittest.main()
