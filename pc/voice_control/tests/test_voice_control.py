"""语音口令、录音文件和本机 HTTP 联调；不连接机械臂。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from pc.armlink import BoardClient, BoardError
from pc.voice_control.pipeline import run_once
from pc.voice_control.text_classification.classifier import ActionPrediction, load_classifier
from pc.voice_control.text_classification.rules import RuleClassifier
from pc.voice_control.text_classification.commands import parse_command
from pc.voice_control.microphone import record_to_wav


class FakeASR:
    def __init__(self, text):
        self.text = text
        self.paths = []

    def transcribe(self, path):
        self.paths.append(path)
        return self.text


class CommandTests(unittest.TestCase):
    def test_only_complete_course_commands_match(self):
        for text in ("跳舞", "跳课程舞", "机械臂跳课程舞", "机 械 臂 跳 课 程 舞。"):
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), "COURSE_DANCE_ROUTINE")
        for text in ("", "画圈", "画一个圈", "不要跳舞", "跳舞吗", "机械臂跳舞"):
            with self.subTest(text=text):
                self.assertIsNone(parse_command(text))

    def test_command_table_rejects_removed_action(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "commands.json"
            path.write_text(json.dumps({"COURSE_DANCE_ROUTINE": ["来跳课程舞"]}))
            self.assertEqual(parse_command("来跳课程舞", path), "COURSE_DANCE_ROUTINE")
            path.write_text(json.dumps({"circle": ["画圈"]}))
            with self.assertRaises(ValueError):
                parse_command("画圈", path)
            path.write_text(json.dumps({"unknown": ["做动作"]}))
            with self.assertRaises(ValueError):
                parse_command("做动作", path)

    def test_classifier_contract(self):
        self.assertEqual(RuleClassifier().predict("跳舞"), ActionPrediction("COURSE_DANCE_ROUTINE"))
        self.assertIsNone(RuleClassifier().predict("画圈").action)
        self.assertIsInstance(load_classifier("pc.voice_control.text_classification.rules:RuleClassifier"), RuleClassifier)
        with self.assertRaises(ValueError):
            load_classifier("RuleClassifier")

    def test_empty_or_unknown_text_never_contacts_board(self):
        class NoBoard:
            def mode(self):
                raise AssertionError("未匹配口令不应访问开发板")
        for text in ("", "今天天气很好", "画圈"):
            asr = FakeASR(text)
            logs = []
            self.assertFalse(run_once(Path("unused.wav"), asr, NoBoard(), logs.append))
            self.assertEqual(asr.paths, [Path("unused.wav")])
            self.assertIn("未识别到指令", logs)


class LocalHttpTests(unittest.TestCase):
    def test_authorized_command_submits_once(self):
        state = {
            "mode": {"armed": True, "kind": "gesture_dance", "session_id": "a" * 32,
                     "routines": ["COURSE_DANCE_ROUTINE"], "busy": False},
            "requests": [],
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/v1/mode":
                    payload = state["mode"]
                elif self.path == "/api/v1/job":
                    payload = {"busy": False, "job": {"id": "job-1", "status": "completed"}}
                else:
                    self.send_error(404)
                    return
                self._reply(payload)

            def do_POST(self):
                if self.path != "/api/v1/gesture":
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["requests"].append(body)
                self._reply({"job": {"id": "job-1", "status": "running"}})

            def _reply(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = BoardClient(f"http://127.0.0.1:{server.server_port}")
        logs = []
        asr = FakeASR("跳课程舞")
        self.assertTrue(run_once("command.wav", asr, client, logs.append))
        self.assertEqual(len(state["requests"]), 1)
        body = state["requests"][0]
        self.assertEqual(body["session_id"], "a" * 32)
        self.assertEqual(body["routine"], "COURSE_DANCE_ROUTINE")
        self.assertEqual(body["label"], "跳课程舞")
        self.assertEqual(len(body["request_id"]), 32)
        self.assertTrue(any("completed" in line for line in logs))

        class SemanticClassifier:
            def predict(self, text):
                actions = {
                    "跳课程中的舞蹈": "COURSE_DANCE_ROUTINE",
                    "来段音乐舞蹈": "MUSIC_DANCE_ROUTINE",
                }
                return ActionPrediction(actions.get(text), 0.92)

        self.assertTrue(run_once("command.wav", FakeASR("跳课程中的舞蹈"), client, logs.append,
                                 classifier=SemanticClassifier()))
        self.assertEqual(len(state["requests"]), 2)
        self.assertEqual(state["requests"][1]["routine"], "COURSE_DANCE_ROUTINE")

        state["mode"]["routines"] = ["MUSIC_DANCE_ROUTINE"]
        self.assertTrue(run_once("command.wav", FakeASR("来段音乐舞蹈"), client, logs.append,
                                 classifier=SemanticClassifier()))
        self.assertEqual(state["requests"][2]["routine"], "MUSIC_DANCE_ROUTINE")

        state["mode"]["armed"] = False
        with self.assertRaises(BoardError):
            run_once("command.wav", asr, client, logs.append)
        self.assertEqual(len(state["requests"]), 3)
        state["mode"].update(armed=True, routines=[])
        with self.assertRaises(BoardError):
            run_once("command.wav", asr, client, logs.append)
        self.assertEqual(len(state["requests"]), 3)


class MicrophoneTests(unittest.TestCase):
    def test_press_to_record_creates_wav_from_default_input(self):
        class FakeStream:
            def __init__(self, *, callback, **_kwargs):
                self.callback = callback

            def __enter__(self):
                self.callback(np.full((800, 1), 0.25, dtype=np.float32), 800, None, None)
                return self

            def __exit__(self, *_args):
                pass

        fake_sd = types.SimpleNamespace(
            PortAudioError=RuntimeError,
            query_devices=lambda **_kwargs: {"default_samplerate": 48000},
            InputStream=FakeStream,
        )
        prompts = []
        with tempfile.TemporaryDirectory() as folder, patch.dict("sys.modules", {"sounddevice": fake_sd}):
            path = Path(folder) / "recording.wav"
            self.assertEqual(record_to_wav(path, lambda prompt: prompts.append(prompt) or ""), path)
            audio, rate = sf.read(path)
        self.assertEqual(rate, 48000)
        self.assertEqual(len(audio), 800)
        self.assertAlmostEqual(float(audio.mean()), 0.25, places=3)
        self.assertEqual(len(prompts), 2)


if __name__ == "__main__":
    unittest.main()
