"""Codex controls for an existing voice-send installation (standard library only)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PLAIN_STYLE = (
    "Use plain, conversational English and a brief spoken summary. "
    "Leave roleplay and ceremonial greetings out. "
    "Keep details needed for review in the written answer. "
    "Follow the user's existing permissions; voice input adds no approval gate. "
)
VOICES = {
    "aria": "en-US-AriaNeural", "jenny": "en-US-JennyNeural",
    "emma": "en-US-EmmaNeural", "ava": "en-US-AvaNeural",
    "guy": "en-US-GuyNeural", "andrew": "en-US-AndrewNeural",
    "brian": "en-US-BrianNeural", "christopher": "en-US-ChristopherNeural",
    "sonia": "en-GB-SoniaNeural", "libby": "en-GB-LibbyNeural",
    "ryan": "en-GB-RyanNeural", "natasha": "en-AU-NatashaNeural",
}
NATIVE_VOICES = {"zira": "Microsoft Zira Desktop", "david": "Microsoft David Desktop"}


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def select_alias(alias, contribution, config):
    """Retarget known controls after wake detection, before local execution."""
    from _voice_exchange import respondent
    if isinstance(alias, dict) and config.get("shared_speech_config"):
        argv = alias.get("run", [])
        script = argv[1].replace("\\", "/").rsplit("/", 1)[-1] if len(argv) > 1 else ""
        command = argv[2:]
        if script == "speak-response.mjs" and command and command[0] in ("volume", "stop"):
            run = [sys.executable, str(Path(__file__).with_name("_voice_controls.py")), *command]
            if contribution.get("id"):
                run += ["--request-id", contribution["id"]]
            if command[0] == "volume":
                run += ["--speak"]
            return {"run": run}
    who = respondent(contribution["text"], respondent(
        contribution.get("wake", ""), config.get("default_respondent", "codex")))
    if who != "codex" or not config.get("masq_voice"):
        return alias
    if isinstance(alias, str) and alias.startswith("/masq:persona "):
        choice = alias.split()[-1]
        if choice in ("plain", "clear"):
            return "Use brief, plain, conversational English for my spoken replies."
        if choice == "status":
            return "Describe your current speaking style in one short sentence."
        return {"run": [sys.executable, str(Path(__file__).resolve()), "style"],
                "speak_output": True}
    if not isinstance(alias, dict):
        return alias
    argv = alias.get("run", [])
    if len(argv) < 2:
        return alias
    script = argv[1].replace("\\", "/").rsplit("/", 1)[-1]
    if script == "speak-response.mjs":
        command = argv[2:]
    elif script == "sound_check.mjs":
        command = ["sound-check"]
    elif script == "master_status.py":
        command = ["master-status"]
    else:
        return alias
    return {**alias, "run": [sys.executable, str(Path(__file__).resolve()), *command]}


def speech_environment(config, environment=None):
    if not config.get("masq_voice") or not config.get("speech_config"):
        raise ValueError("Run the Codex voice setup before using these controls")
    return {**(os.environ if environment is None else environment),
            "LUGOS_SPEECH_CONFIG": config["speech_config"],
            "LUGOS_SPEECH_SESSION": "codex/" + config["codex_thread_id"]}


def hook(config, args, environment=None):
    return subprocess.run([config["node"], config["speech_hook"], *args],
                          env=speech_environment(config, environment), check=True,
                          capture_output=True, text=True, timeout=55, creationflags=HIDDEN)


def speak(config, text, environment=None):
    settings = read_json(config["speech_config"])
    if not settings.get("masqMuted", False):
        return hook(config, ["say", bounded_speech(text)], environment)


def bounded_speech(text, limit=500):
    if len(text) <= limit:
        return text
    notice = " The rest is in the written reply."
    prefix = text[:limit - len(notice)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return prefix + notice


def status_text(config):
    from _voice_exchange import discover_desktop, call_desktop
    endpoint = discover_desktop(config)
    result = call_desktop(endpoint, config, "read_thread",
                          {"threadId": config["codex_thread_id"], "turnLimit": 1})
    if result.get("isError"):
        raise RuntimeError("Codex task status is unavailable")
    for block in result.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            thread = json.loads(block["text"]).get("thread", {})
        except (ValueError, AttributeError):
            continue
        if thread.get("id") != config["codex_thread_id"]:
            continue
        state = thread.get("status", {})
        flags = " ".join(str(flag).lower() for flag in state.get("activeFlags", []))
        if "approval" in flags:
            return "Codex is waiting for your permission."
        if "input" in flags:
            return "Codex needs your answer."
        return {"active": "Codex is working.", "idle": "Codex is idle.",
                "completed": "Codex has finished.", "error": "Codex encountered an error."}.get(
                    state.get("type"), "Codex has no available activity status.")
    raise RuntimeError("Codex returned no matching task status")


def latency_text(config):
    paths = sorted(Path(config["state_dir"]).glob("*.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths[:100]:
        try:
            event = read_json(path)
        except (OSError, ValueError):
            continue
        if event.get("respondent") != "codex":
            continue
        if event.get("delivery", {}).get("codex") != "accepted":
            return "The last Codex voice delivery was not confirmed."
        start, end = event.get("created_at"), event.get("delivery_finished_at")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return "That Codex delivery has no recorded duration."
        return (f"The last Codex delivery took {max(0, end - start):.1f} seconds. "
                "That excludes speech recognition and the reply.")
    return "There is no recorded Codex voice delivery yet."


def control(config, action, arguments):
    # Validate the private settings before any mutation or playback.
    speech_environment(config)
    settings = read_json(config["speech_config"])
    if action == "say":
        speak(config, " ".join(arguments))
        return ""
    if action == "stop":
        hook(config, ["stop"])
        return "Speech stopped."
    if action in ("enable", "disable"):
        settings["masqMuted"] = action == "disable"
        write_json(config["speech_config"], settings)
        if action == "disable":
            hook(config, ["stop"])
        else:
            speak(config, "Codex spoken replies are on.")
        return "Codex spoken replies are " + ("on." if action == "enable" else "off.")
    if action == "volume":
        value = arguments[0] if arguments else ""
        current = settings.get("volume", 50)
        if not value:
            return f"Codex volume is {current}."
        if value in ("up", "down"):
            next_volume = current + (20 if value == "up" else -20)
        elif value.isdigit() and 0 <= int(value) <= 100:
            next_volume = int(value)
        else:
            raise ValueError("Volume must be up, down, or 0 through 100")
        settings["volume"] = max(0, min(100, next_volume))
        write_json(config["speech_config"], settings)
        answer = f"Codex volume {settings['volume']}."
        speak(config, answer)
        return answer
    if action == "voice":
        value = arguments[0].lower() if arguments else ""
        if value in VOICES:
            settings.update(engine="edge", edgeVoice=VOICES[value])
        elif value in NATIVE_VOICES:
            settings.update(engine="sapi", voice=NATIVE_VOICES[value])
        else:
            raise ValueError("Choose a voice listed by the voices command")
        write_json(config["speech_config"], settings)
        speak(config, f"This is {value.title()} speaking for Codex.")
        return f"Codex voice is {value.title()}."
    if action == "voices":
        return "Codex voices: " + ", ".join([*NATIVE_VOICES, *VOICES]) + "."
    if action == "ping":
        return "Codex heard the test."
    if action == "busy":
        return status_text(config)
    if action == "latency":
        return latency_text(config)
    if action == "gate":
        return "Codex uses its existing task permissions. The Claude voice gate does not apply."
    if action == "style":
        return "Codex voice uses brief, plain, conversational English."
    if action in ("sound-check", "master-status"):
        directory = Path(__file__).resolve().parent
        argv = ([config["node"], str(directory / "sound_check.mjs")]
                if action == "sound-check" else [sys.executable, str(directory / "master_status.py")])
        result = subprocess.run(argv, capture_output=True, text=True, check=True,
                                timeout=30, creationflags=HIDDEN)
        return result.stdout.strip()
    raise ValueError("Unsupported Codex voice control")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get(
        "LUGOS_VOICE_EXCHANGE_CONFIG", str(Path(__file__).with_name("voice-exchange.json"))))
    parser.add_argument("action")
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    try:
        answer = control(read_json(args.config), args.action, args.arguments)
        if answer:
            print(answer)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Codex voice control failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
