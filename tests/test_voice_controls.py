import concurrent.futures
import contextlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration"))
sys.path.insert(0, str(ROOT / "scripts"))
import _voice_controls as controls
import _masq_voice as voice
import setup_codex as setup


class SharedControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude.json"
        self.codex = self.root / "codex.json"
        self.config = {"shared_speech_config": str(self.claude), "speech_config": str(self.codex),
                       "voice_control_state_dir": str(self.root / "controls")}
        for path, name in ((self.claude, "Voice A"), (self.codex, "Voice B")):
            controls.write(path, {"volume": 5, "voice": name, "outputDevice": "Example speakers",
                                  "speechFloor": {"url": "private", "tokenFile": "private-reference"}})

    def test_common_five_point_step_preserves_voice_and_route(self):
        result = controls.volume(self.config, "up", "turn-a")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["target"], 10)
        for path, name in ((self.claude, "Voice A"), (self.codex, "Voice B")):
            cfg = controls.read(path)
            self.assertEqual(cfg["volume"], 10)
            self.assertEqual(cfg["voice"], name)
            self.assertEqual(cfg["outputDevice"], "Example speakers")
            self.assertEqual(cfg["speechFloor"]["tokenFile"], "private-reference")

    def test_duplicate_relative_request_is_not_applied_twice(self):
        controls.volume(self.config, "up", "same-turn")
        again = controls.volume(self.config, "up", "same-turn")
        self.assertTrue(again["replayed"])
        self.assertEqual(controls.read(self.claude)["volume"], 10)
        with self.assertRaises(ValueError):
            controls.volume(self.config, "down", "same-turn")

    def test_concurrent_relative_changes_are_serialized(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda request: controls.volume(self.config, "up", request), ("a", "b")))
        self.assertEqual(sorted(r["target"] for r in results), [10, 15])
        self.assertEqual(controls.levels(controls.sources(self.config)), {"claude": [15], "codex": [15]})

    def test_partial_write_is_visible_and_never_retried_as_relative_change(self):
        original_write = controls.write

        def fail_second(path, *args, **kwargs):
            # Windows runner temp paths can use an 8.3 alias; production resolves it.
            if Path(path).resolve() == self.codex.resolve():
                raise PermissionError("test failure")
            return original_write(path, *args, **kwargs)

        with patch.object(controls, "write", side_effect=fail_second):
            result = controls.volume(self.config, "up", "partial-turn")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["observed"], {"claude": [10], "codex": [5]})
        again = controls.volume(self.config, "up", "partial-turn")
        self.assertEqual(again["status"], "partial")
        self.assertEqual(controls.read(self.claude)["volume"], 10)
        self.assertEqual(controls.read(self.codex)["volume"], 5)

    def test_drift_requires_explicit_level_and_profile_overrides_are_read_back(self):
        cfg = controls.read(self.claude)
        cfg["sessionVoices"] = {"example-session": {"volume": 30, "voice": "Different voice"}}
        controls.write(self.claude, cfg)
        with self.assertRaises(ValueError):
            controls.volume(self.config, "up", "drift")
        result = controls.volume(self.config, "5", "align")
        self.assertEqual(result["observed"], {"claude": [5, 5], "codex": [5]})
        self.assertEqual(controls.read(self.claude)["sessionVoices"]["example-session"]["voice"], "Different voice")

    def test_floor_and_ceiling_include_zero_without_changing_mute(self):
        controls.volume(self.config, "down", "zero")
        self.assertEqual(controls.read(self.codex)["volume"], 0)
        controls.volume(self.config, "100", "maximum")
        controls.volume(self.config, "up", "clamped")
        self.assertEqual(controls.read(self.codex)["volume"], 100)
        with self.assertRaises(ValueError):
            controls.volume(self.config, "101", "invalid")

    def test_both_respondents_route_volume_and_stop_to_the_shared_control(self):
        for who in ("claude", "codex"):
            module = types.SimpleNamespace(respondent=lambda *_args: who)
            with patch.dict(sys.modules, {"_voice_exchange": module}):
                for command in (["volume", "up"], ["stop"]):
                    alias = {"run": ["node", "example/speak-response.mjs", *command], "speak_output": True}
                    selected = voice.select_alias(alias, {"id": "original-turn", "text": "request"}, self.config)
                    self.assertEqual(Path(selected["run"][1]).name, "_voice_controls.py")
                    self.assertIn("original-turn", selected["run"])
                    self.assertNotIn("speak_output", selected)
                    self.assertEqual("--speak" in selected["run"], command[0] == "volume")

    def test_long_speech_announces_continuation_and_short_speech_is_unchanged(self):
        text = "This is a useful sentence. " * 100
        result = voice.bounded_speech(text)
        self.assertLessEqual(len(result), 500)
        self.assertTrue(result.endswith("The rest is in the written reply."))
        self.assertEqual(voice.bounded_speech(result), result)
        self.assertEqual(voice.bounded_speech("Short answer."), "Short answer.")

    def test_private_profile_retains_floor_references_but_not_credentials(self):
        cfg = setup.private_speech_settings({"speechFloor": {
            "url": "http://localhost:1234/floor", "tokenFile": "example-token-reference",
            "host": "example-node", "token": "must-not-copy"}, "token": "must-not-copy"}, "old")
        self.assertEqual(cfg["speechFloor"], {"url": "http://localhost:1234/floor",
                         "tokenFile": "example-token-reference", "host": "example-node"})
        self.assertNotIn("token", cfg)

    def test_feedback_timeout_does_not_hide_verified_volume_change(self):
        config = self.root / "exchange.json"
        controls.write(config, self.config)
        output = io.StringIO()
        with patch.object(sys, "argv", ["controls", "--config", str(config), "volume", "up", "--speak"]), \
                patch.object(controls, "hook", side_effect=subprocess.TimeoutExpired("test", 60)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(controls.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["feedback"], "unconfirmed")
        self.assertEqual(controls.read(self.codex)["volume"], 10)

    def test_installed_control_vocabulary_matches_public_source(self):
        aliases = json.loads((ROOT / "aliases.example.json").read_text(encoding="utf-8"))
        installed = setup.control_aliases({"custom": "Keep this custom command"}, "example/speak-response.mjs")
        self.assertEqual(installed.pop("custom"), "Keep this custom command")
        for phrase, command in installed.items():
            self.assertEqual(command["run"][2:], aliases[phrase]["run"][2:])
        self.assertEqual(installed["voice louder"]["run"][2:], ["volume", "up"])
        self.assertEqual(installed["voice quieter"]["run"][2:], ["volume", "down"])
        self.assertEqual(installed["stop speaking"]["run"][2:], ["stop"])


if __name__ == "__main__":
    unittest.main()
