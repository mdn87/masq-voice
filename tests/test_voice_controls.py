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
                       "voice_control_state_dir": str(self.root / "controls"), "node": "node",
                       "speech_hook": "example/speak-response.mjs", "codex_thread_id": "task-example"}
        for path, name in ((self.claude, "Voice A"), (self.codex, "Voice B")):
            controls.write(path, {"volume": 5, "voice": name, "outputDevice": "Example speakers",
                                  "speechFloor": {"url": "private", "tokenFile": "private-reference"}})

    def test_default_ten_point_step_preserves_voice_and_route(self):
        result = controls.volume(self.config, "up", "turn-a")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["target"], 15)
        for path, name in ((self.claude, "Voice A"), (self.codex, "Voice B")):
            cfg = controls.read(path)
            self.assertEqual(cfg["volume"], 15)
            self.assertEqual(cfg["voice"], name)
            self.assertEqual(cfg["outputDevice"], "Example speakers")
            self.assertEqual(cfg["speechFloor"]["tokenFile"], "private-reference")

    def test_duplicate_relative_request_is_not_applied_twice(self):
        controls.volume(self.config, "up", "same-turn")
        again = controls.volume(self.config, "up", "same-turn")
        self.assertTrue(again["replayed"])
        self.assertEqual(controls.read(self.claude)["volume"], 15)
        with self.assertRaises(ValueError):
            controls.volume(self.config, "down", "same-turn")

    def test_concurrent_relative_changes_are_serialized(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda request: controls.volume(self.config, "up", request), ("a", "b")))
        self.assertEqual(sorted(r["target"] for r in results), [15, 25])
        self.assertEqual(controls.levels(controls.sources(self.config)), {"claude": [25], "codex": [25]})

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
        self.assertEqual(result["observed"], {"claude": [15], "codex": [5]})
        again = controls.volume(self.config, "up", "partial-turn")
        self.assertEqual(again["status"], "partial")
        self.assertEqual(controls.read(self.claude)["volume"], 15)
        self.assertEqual(controls.read(self.codex)["volume"], 5)

    def test_a_session_override_keeps_its_offset_and_an_explicit_level_aligns(self):
        cfg = controls.read(self.claude)
        cfg["sessionVoices"] = {"example-session": {"volume": 30, "voice": "Different voice"}}
        controls.write(self.claude, cfg)
        stepped = controls.volume(self.config, "up", "drift")
        self.assertEqual(stepped["observed"], {"claude": [15, 40], "codex": [15]})
        result = controls.volume(self.config, "5", "align")
        self.assertEqual(result["observed"], {"claude": [5, 5], "codex": [5]})
        self.assertEqual(controls.read(self.claude)["sessionVoices"]["example-session"]["voice"], "Different voice")

    def test_floor_and_ceiling_include_zero_without_changing_mute(self):
        controls.volume(self.config, "down", "zero")
        self.assertEqual(controls.read(self.codex)["volume"], 0)
        controls.volume(self.config, "100", "maximum")
        controls.volume(self.config, "up", "clamped")
        self.assertEqual(controls.read(self.codex)["volume"], 100)
        controls.volume(self.config, "40", "reset")
        self.assertEqual(controls.volume(self.config, "101", "too-high")["levels"], {"claude": 100, "codex": 100})
        with self.assertRaises(ValueError):
            controls.volume(self.config, "loud", "invalid")

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
                    if command[0] == "volume":
                        run = selected["run"]
                        self.assertEqual(run[run.index("--addressed") + 1], who)
                        self.assertEqual(run[run.index("--scope") + 1], "both")

    def levels_now(self):
        return controls.read(self.claude)["volume"], controls.read(self.codex)["volume"]

    def set_both(self, claude, codex):
        for path, level in ((self.claude, claude), (self.codex, codex)):
            cfg = controls.read(path)
            cfg["volume"] = level
            controls.write(path, cfg)

    def run_cli(self, arguments):
        config = self.root / "exchange.json"
        controls.write(config, self.config)
        output = io.StringIO()
        with patch.object(sys, "argv", ["controls", "--config", str(config), *arguments]), \
                patch.object(controls, "hook") as hook, contextlib.redirect_stdout(output):
            hook.return_value = types.SimpleNamespace(returncode=0)
            code = controls.main()
        return code, json.loads(output.getvalue()), hook

    def test_turn_yourself_up_addressed_to_codex_changes_codex_only_with_one_confirmation(self):
        module = types.SimpleNamespace(respondent=lambda *_args: "codex")
        alias = {"run": ["node", "example/speak-response.mjs", "volume", "up"], "scope": "self"}
        with patch.dict(sys.modules, {"_voice_exchange": module}):
            selected = voice.select_alias(alias, {"id": "turn-1", "text": "turn yourself up"}, self.config)
        code, result, hook = self.run_cli(selected["run"][2:])
        self.assertEqual((code, result["applied"]), (0, True))
        self.assertEqual(self.levels_now(), (5, 15))
        hook.assert_called_once_with(self.config, ["say", "Level 15."], "codex", "turn-1")

    def test_a_spoken_pronoun_sets_the_scope_when_the_alias_has_none(self):
        module = types.SimpleNamespace(respondent=lambda *_args: "claude")
        alias = {"run": ["node", "example/speak-response.mjs", "volume", "down"]}
        for text, scope in (("can you turn yourself down", "self"), ("turn your voice down", "self"),
                            ("volume down", "both"), ("everyone quieter", "both")):
            with patch.dict(sys.modules, {"_voice_exchange": module}):
                run = voice.select_alias(alias, {"id": "t", "text": text}, self.config)["run"]
            self.assertEqual(run[run.index("--scope") + 1], scope, text)

    def test_volume_down_changes_both_voices(self):
        self.set_both(30, 30)
        result = controls.dispatch(self.config, "volume", "down", "both", "turn-2")
        self.assertEqual(self.levels_now(), (20, 20))
        self.assertEqual(result["confirmation"], "Both at 20.")

    def test_the_same_contribution_twice_applies_and_confirms_once(self):
        with patch.object(controls, "hook") as hook:
            hook.return_value = types.SimpleNamespace(returncode=0)
            first = controls.dispatch(self.config, "volume", "up", "both", "turn-3", speak=True)
            second = controls.dispatch(self.config, "volume", "up", "both", "turn-3", speak=True)
        self.assertEqual((first["applied"], second["applied"]), (True, False))
        self.assertIsNone(second["confirmation"])
        self.assertEqual(self.levels_now(), (15, 15))
        hook.assert_called_once_with(self.config, ["say", "Both at 15."], "claude", "turn-3")

    def test_everyone_up_steps_each_profile_from_its_own_level(self):
        self.set_both(25, 5)
        result = controls.dispatch(self.config, "volume", "up", "both", "turn-4")
        self.assertEqual(self.levels_now(), (35, 15))
        self.assertEqual(result["confirmation"], "Claude at 35, Codex at 15.")

    def test_set_yourself_to_forty_is_absolute_for_the_addressed_voice(self):
        self.set_both(25, 5)
        result = controls.dispatch(self.config, "volume", "40", "self", "turn-5", addressed="claude")
        self.assertEqual(self.levels_now(), (40, 5))
        self.assertEqual(result["confirmation"], "Level 40.")

    def test_a_level_above_one_hundred_is_clamped(self):
        code, result, _hook = self.run_cli(["volume", "101", "--scope", "self", "--addressed", "codex"])
        self.assertEqual((code, result["levels"]), (0, {"codex": 100}))
        self.assertEqual(self.levels_now(), (5, 100))

    def test_the_step_comes_from_the_exchange_configuration(self):
        self.config["volume_step"] = 5
        self.assertEqual(controls.volume(self.config, "up", "small")["levels"], {"claude": 10, "codex": 10})
        self.config["volume_step"] = 0
        with self.assertRaises(ValueError):
            controls.volume(self.config, "up", "invalid-step")

    def test_a_confirmation_at_zero_is_not_spoken(self):
        with patch.object(controls, "hook") as hook:
            result = controls.dispatch(self.config, "volume", "down", "self", "turn-6", addressed="codex", speak=True)
        self.assertEqual((result["feedback"], self.levels_now()), ("silent_at_zero", (5, 0)))
        hook.assert_not_called()

    def test_a_reading_changes_nothing_and_writes_no_receipt(self):
        self.set_both(25, 5)
        result = controls.dispatch(self.config, "status", "", "both", "turn-7")
        self.assertEqual((result["applied"], result["confirmation"]), (False, "Claude at 25, Codex at 5."))
        self.assertFalse((self.root / "controls").exists() and any((self.root / "controls").glob("*.json")))

    def test_a_codex_only_install_changes_its_one_profile(self):
        del self.config["shared_speech_config"]
        result = controls.volume(self.config, "up", "codex-only", "both", "codex")
        self.assertEqual(result["levels"], {"codex": 15})
        self.assertEqual(controls.read(self.claude)["volume"], 5)

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
        self.assertEqual(controls.read(self.codex)["volume"], 15)

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
