"""Prepare or install Codex controls into an existing Windows voice-send listener."""
import argparse
import ast
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "integration"))
from _masq_voice import read_json


def replace_once(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise ValueError("Unsupported listener version; expected one anchor: " + old[:70])
    return text.replace(old, new, 1)


def prepare_listener(text):
    text = replace_once(text, "def main_loop(args, tr, notes, wake, utter_q):",
                        "from _masq_voice import select_alias\n\n\ndef main_loop(args, tr, notes, wake, utter_q):")
    text = replace_once(text,
                        "        alias = match_alias(cmd, aliases)\n        if isinstance(alias, dict):",
                        "        alias = match_alias(cmd, aliases)\n"
                        "        if exchange.enabled:\n"
                        "            contribution = contribution or exchange_payload(cmd, text, stamp=speech_floor.snapshot)\n"
                        "            alias = select_alias(alias, contribution, exchange.config)\n"
                        "        if isinstance(alias, dict):")
    ast.parse(text)
    return text


def prepare_exchange(text):
    text = replace_once(text, '        if recipient == "codex":\n            policy += (',
                        '        if recipient == "codex":\n'
                        '            if self.config.get("masq_voice"):\n'
                        '                from _masq_voice import PLAIN_STYLE\n'
                        '                policy += " " + PLAIN_STYLE\n'
                        '            policy += (')
    text = replace_once(text,
                        '        subprocess.run([self.config["node"], self.config["speech_hook"], "say", text[:500]],',
                        '        if self.config.get("masq_voice"):\n'
                        '            from _masq_voice import speak\n'
                        '            speak(self.config, text[:500], environment)\n'
                        '            return\n'
                        '        subprocess.run([self.config["node"], self.config["speech_hook"], "say", text[:500]],')
    text = replace_once(text,
                        '        self.save(event)\n        return event\n\n    def say(',
                        '        event["delivery_finished_at"] = time.time()\n'
                        '        self.save(event)\n        return event\n\n    def say(')
    ast.parse(text)
    return text


def private_speech_settings(source, old_thread):
    # Copy playback settings only, never hooks, approval state or service credentials.
    fields = ("engine", "edgeVoice", "voice", "rate", "edgeRate", "volume",
              "outputDevice", "edgePython", "player", "summary", "summaryChars",
              "summarySentences", "maxChars")
    result = {key: source[key] for key in fields if key in source}
    profile = source.get("sessionVoices", {}).get("codex/" + old_thread, {})
    if not profile:
        result.update(engine="edge", edgeVoice="en-US-GuyNeural", voice="Microsoft David Desktop")
    for key in ("engine", "edgeVoice", "voice", "rate", "edgeRate", "volume"):
        if key in profile:
            result[key] = profile[key]
    result.setdefault("engine", "edge")
    result.setdefault("edgeVoice", "en-US-GuyNeural")
    result.setdefault("voice", "Microsoft David Desktop")
    result.setdefault("volume", 50)
    result["masqMuted"] = False
    return result


def prepare(directory, thread_id, source_path):
    # A UUID pins one task; never infer a task from whichever window has focus.
    uuid.UUID(thread_id)
    directory = Path(directory).resolve()
    exchange_path = directory / "voice-exchange.json"
    config = read_json(exchange_path)
    for key in ("node", "speech_hook", "state_dir"):
        if not config.get(key):
            raise ValueError("Missing voice-exchange setting: " + key)
    listener_path = directory / "voice_send.py"
    exchange_code = directory / "_voice_exchange.py"
    changes = {
        listener_path: prepare_listener(listener_path.read_text(encoding="utf-8-sig")),
        exchange_code: prepare_exchange(exchange_code.read_text(encoding="utf-8-sig")),
        directory / "_masq_voice.py": (ROOT / "integration/_masq_voice.py").read_text(encoding="utf-8"),
    }
    speech_path = directory / ".local/masq-voice/codex-tts.json"
    if speech_path.exists():
        speech = read_json(speech_path)
    else:
        speech = private_speech_settings(read_json(source_path), config.get("codex_thread_id", ""))
    changes[speech_path] = json.dumps(speech, indent=2) + "\n"
    config.update(enabled=True, codex_thread_id=thread_id, default_respondent="codex",
                  broadcast_observations=False, speech_config=speech_path.as_posix(), masq_voice=True)
    changes[exchange_path] = json.dumps(config, indent=2) + "\n"
    return changes


def install(directory, changes):
    directory = Path(directory).resolve()
    backup = directory / ".local/masq-voice/backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    # Back up every existing target before the first replacement.
    for path in changes:
        if not path.is_relative_to(directory):
            raise ValueError("Installation target escaped the listener directory")
        if path.exists():
            target = backup / path.relative_to(directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for path, text in changes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".masq.tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listener-dir", required=True, type=Path)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--speech-source", required=True, type=Path,
                        help="Existing TTS config; read only to seed private Codex settings")
    parser.add_argument("--apply", action="store_true", help="Write files after stopping the listener through its guardian")
    args = parser.parse_args()
    try:
        changes = prepare(args.listener_dir, args.thread_id, args.speech_source)
        if args.apply:
            backup = install(args.listener_dir, changes)
            print(f"Installed Codex voice controls. Backup: {backup}")
        else:
            print("Preview only; validated files to update:")
            for path in changes:
                print(path.relative_to(args.listener_dir.resolve()))
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, f"Setup failed: {error}\n")


if __name__ == "__main__":
    main()
