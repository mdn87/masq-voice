import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "integration"))
sys.path.insert(0, str(ROOT / "scripts"))
import _masq_voice as voice
import setup_codex as setup

# Minimal supported integration boundaries; no microphone, app, or model required.
LISTENER = '''def main_loop(args, tr, notes, wake, utter_q):
    while True:
        alias = match_alias(cmd, aliases)
        if isinstance(alias, dict):
            run_alias(alias, cmd)
'''
EXCHANGE = '''import subprocess
import time
class VoiceExchange:
    def message(self, event, recipient):
        policy = "Reply briefly."
        if recipient == "codex":
            policy += (" Speak once.")
        return policy
    def dispatch(self, event):
        self.save(event)
        return event

    def say(self, text, environment):
        subprocess.run([self.config["node"], self.config["speech_hook"], "say", text[:500]],
                       env=environment)
'''


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.speech = self.root / "codex-tts.json"
        self.source = self.root / "claude-tts.json"
        voice.write_json(self.source, {"volume": 70, "engine": "edge"})
        voice.write_json(self.speech, {"volume": 50, "engine": "edge", "masqMuted": False})
        self.config = {"masq_voice": True, "codex_thread_id": "task-example",
                       "speech_config": str(self.speech), "speech_hook": "speech-hook.mjs",
                       "node": "node", "state_dir": str(self.root / "receipts")}
        self.hook = patch.object(voice, "hook").start()
        self.addCleanup(patch.stopall)

    def test_volume_changes_only_private_codex_settings(self):
        before = self.source.read_bytes()
        voice.control(self.config, "volume", ["down"])
        self.assertEqual(voice.read_json(self.speech)["volume"], 30)
        self.assertEqual(self.source.read_bytes(), before)
        self.hook.assert_called_once_with(self.config, ["say", "Codex volume 30."], None)

    def test_volume_clamps_and_rejects_invalid_input(self):
        voice.control(self.config, "volume", ["0"])
        voice.control(self.config, "volume", ["down"])
        self.assertEqual(voice.read_json(self.speech)["volume"], 0)
        with self.assertRaises(ValueError):
            voice.control(self.config, "volume", ["101"])

    def test_mute_blocks_reply_and_keeps_global_settings(self):
        before = self.source.read_bytes()
        voice.control(self.config, "disable", [])
        self.hook.assert_called_once_with(self.config, ["stop"])
        self.hook.reset_mock()
        voice.speak(self.config, "A reply")
        voice.control(self.config, "volume", ["down"])
        voice.control(self.config, "voice", ["guy"])
        self.hook.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)
        voice.control(self.config, "enable", [])
        self.assertFalse(voice.read_json(self.speech)["masqMuted"])
        self.hook.assert_called_once()

    def test_voice_selects_neural_and_native_engines(self):
        voice.control(self.config, "voice", ["sonia"])
        self.assertEqual(voice.read_json(self.speech)["edgeVoice"], "en-GB-SoniaNeural")
        voice.control(self.config, "voice", ["david"])
        settings = voice.read_json(self.speech)
        self.assertEqual(settings["engine"], "sapi")
        self.assertEqual(settings["voice"], "Microsoft David Desktop")

    def test_speech_keeps_original_floor_stamp(self):
        environment = voice.speech_environment(self.config, {"LUGOS_SPEECH_STAMP": "original"})
        self.assertEqual(environment["LUGOS_SPEECH_STAMP"], "original")
        self.assertEqual(environment["LUGOS_SPEECH_SESSION"], "codex/task-example")
        self.assertEqual(environment["LUGOS_SPEECH_CONFIG"], str(self.speech))

    def test_unconfigured_controls_fail_before_mutation(self):
        with self.assertRaises(ValueError):
            voice.control({}, "disable", [])
        self.hook.assert_not_called()

    def test_status_reads_only_the_pinned_task(self):
        desktop = types.SimpleNamespace(
            discover_desktop=Mock(return_value={"endpoint": "example"}),
            call_desktop=Mock(return_value={"content": [{"type": "text", "text": json.dumps({
                "thread": {"id": "task-example", "status": {"type": "active", "activeFlags": []}}
            })}]}))
        with patch.dict(sys.modules, {"_voice_exchange": desktop}):
            self.assertEqual(voice.control(self.config, "busy", []), "Codex is working.")
        self.assertEqual(desktop.call_desktop.call_args.args[2], "read_thread")
        self.assertEqual(desktop.call_desktop.call_args.args[3]["threadId"], "task-example")

    def test_wrong_task_status_is_rejected(self):
        desktop = types.SimpleNamespace(discover_desktop=Mock(return_value={}),
            call_desktop=Mock(return_value={"content": [{"type": "text", "text": json.dumps({
                "thread": {"id": "another-task", "status": {"type": "idle"}}
            })}]}))
        with patch.dict(sys.modules, {"_voice_exchange": desktop}), self.assertRaises(RuntimeError):
            voice.status_text(self.config)

    def test_latency_reports_delivery_only(self):
        voice.write_json(Path(self.config["state_dir"]) / "receipt.json", {
            "respondent": "codex", "delivery": {"codex": "accepted"},
            "created_at": 100, "delivery_finished_at": 102.5})
        answer = voice.latency_text(self.config)
        self.assertIn("2.5 seconds", answer)
        self.assertIn("excludes speech recognition", answer)

    def test_gate_does_not_change_permissions_or_call_claude(self):
        before = self.speech.read_bytes()
        self.assertIn("existing task permissions", voice.control(self.config, "gate", ["off"]))
        self.assertEqual(before, self.speech.read_bytes())
        self.hook.assert_not_called()


class AliasTests(unittest.TestCase):
    def selected(self, alias, who):
        module = types.SimpleNamespace(respondent=lambda text, default="codex": who)
        with patch.dict(sys.modules, {"_voice_exchange": module}):
            return voice.select_alias(alias, {"text": "volume down", "wake": "hey " + who},
                                      {"masq_voice": True})

    def test_codex_controls_use_adapter_and_claude_controls_stay_intact(self):
        aliases = voice.read_json(ROOT / "aliases.example.json")
        for phrase, original in aliases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(self.selected(original, "claude"), original)
                selected = self.selected(original, "codex")
                if isinstance(original, dict):
                    self.assertEqual(Path(selected["run"][1]).name, "_masq_voice.py")
                    self.assertEqual(selected.get("speak_output"), original.get("speak_output"))
                elif original.startswith("/masq:persona"):
                    self.assertFalse(isinstance(selected, str) and selected.startswith("/"))
                else:
                    self.assertEqual(selected, original)

    def test_custom_local_actions_are_preserved(self):
        original = {"run": ["python", "custom.py", "arg"]}
        self.assertEqual(self.selected(original, "codex"), original)

    def test_plain_style_does_not_send_a_claude_slash_command(self):
        self.assertIn("plain", self.selected("/masq:persona plain", "codex"))


class SetupTests(unittest.TestCase):
    def test_patches_are_idempotent(self):
        listener = setup.prepare_listener(LISTENER)
        exchange = setup.prepare_exchange(EXCHANGE)
        self.assertEqual(setup.prepare_listener(listener), listener)
        self.assertEqual(setup.prepare_exchange(exchange), exchange)

    def test_unknown_listener_rejected(self):
        with self.assertRaises(ValueError):
            setup.prepare_listener("def main(): pass\n")

    def test_private_settings_flatten_profile_without_copying_service_data(self):
        source = {"volume": 70, "outputDevice": "Example output", "token": "fake-secret",
                  "sessionVoices": {"codex/old-task": {"volume": 30, "edgeVoice": "en-US-GuyNeural"}}}
        settings = setup.private_speech_settings(source, "old-task")
        self.assertEqual(settings["volume"], 30)
        self.assertEqual(settings["outputDevice"], "Example output")
        self.assertNotIn("sessionVoices", settings)
        self.assertNotIn("token", settings)

    def test_new_codex_task_gets_distinct_default_voice(self):
        settings = setup.private_speech_settings(
            {"volume": 50, "edgeVoice": "en-GB-SoniaNeural", "voice": "Microsoft Zira Desktop"}, "unassigned")
        self.assertEqual(settings["edgeVoice"], "en-US-GuyNeural")
        self.assertEqual(settings["voice"], "Microsoft David Desktop")
        self.assertEqual(settings["volume"], 50)

    def test_prepare_does_not_write_and_install_backs_up_all_existing_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "voice_send.py").write_text(LISTENER, encoding="utf-8")
            (root / "_voice_exchange.py").write_text(EXCHANGE, encoding="utf-8")
            voice.write_json(root / "voice-exchange.json", {"node": "node", "speech_hook": "hook.mjs",
                "state_dir": "receipts", "claude_session_title": "Example voice task"})
            voice.write_json(root / "source.json", {"volume": 40})
            changes = setup.prepare(root, "00000000-0000-4000-8000-000000000001", root / "source.json")
            self.assertEqual((root / "voice_send.py").read_text(), LISTENER)
            backup = setup.install(root, changes)
            self.assertEqual((backup / "voice_send.py").read_text(), LISTENER)
            config = voice.read_json(root / "voice-exchange.json")
            self.assertEqual(config["claude_session_title"], "Example voice task")
            self.assertFalse(config["broadcast_observations"])
            settings_path = Path(config["speech_config"])
            settings = voice.read_json(settings_path)
            settings.update(volume=20, masqMuted=True)
            voice.write_json(settings_path, settings)
            again = setup.prepare(root, config["codex_thread_id"], root / "source.json")
            self.assertEqual(json.loads(again[settings_path])["volume"], 20)
            self.assertTrue(json.loads(again[settings_path])["masqMuted"])

    def test_patched_reply_obeys_private_mute_and_preserves_stamp(self):
        scope = {}
        exec(setup.prepare_exchange(EXCHANGE), scope)
        exchange = scope["VoiceExchange"]()
        exchange.config = {"masq_voice": True}
        with patch.object(voice, "speak") as speak:
            exchange.say("A short reply", {"LUGOS_SPEECH_STAMP": "original"})
        speak.assert_called_once_with(exchange.config, "A short reply", {"LUGOS_SPEECH_STAMP": "original"})
        self.assertIn("plain, conversational English", exchange.message({}, "codex"))


if __name__ == "__main__":
    unittest.main()
