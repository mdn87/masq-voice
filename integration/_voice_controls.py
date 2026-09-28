"""One local voice-level control for the two configured speech profiles.

This is the only writer of speech levels. The listener, the Codex bridge and the speech
hook all call it. A command names a scope: "self" changes only the addressed assistant's
profile, and "both" changes every configured profile. A relative change steps each profile
from its own level by the configured volume_step, so profiles that disagree still move.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_STEP = 10
SCOPES = ("self", "both")


def read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a speech configuration object")
    return value


def write(path, value, expected=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected is not None and path.read_bytes() != expected:
        raise RuntimeError("Speech settings changed during the operation")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    if expected is not None and path.read_bytes() != expected:
        temporary.unlink()
        raise RuntimeError("Speech settings changed during the operation")
    temporary.replace(path)


@contextmanager
def writer_lock(path, timeout=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another voice-level change is still running")
                time.sleep(0.025)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def sources(config):
    """The configured profiles. A Codex-only install has just the "codex" entry."""
    paths = {}
    if config.get("shared_speech_config"):
        paths["claude"] = Path(config["shared_speech_config"]).resolve()
    if config.get("speech_config"):
        paths["codex"] = Path(config["speech_config"]).resolve()
    if not paths:
        raise ValueError("No speech profile is configured")
    if len(paths) == 2 and paths["claude"] == paths["codex"]:
        raise ValueError("The two voice profiles must have separate files")
    return paths


def step_size(config):
    step = config.get("volume_step", DEFAULT_STEP)
    if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= 50:
        raise ValueError("volume_step must be a whole number from 1 to 50")
    return step


def clamp(level):
    return max(0, min(100, int(level)))


def file_levels(cfg):
    values = [cfg.get("volume", 100)]
    profiles = cfg.get("sessionVoices", {})
    if isinstance(profiles, dict):
        values += [p["volume"] for p in profiles.values()
                   if isinstance(p, dict) and "volume" in p]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100 for v in values):
        raise ValueError("Invalid configured voice level")
    return values


def levels(paths):
    return {name: file_levels(read(path)) for name, path in paths.items()}


def all_at(observed, level):
    return all(value == level for values in observed.values() for value in values)


def set_level(cfg, level):
    cfg["volume"] = level
    profiles = cfg.get("sessionVoices", {})
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict) and "volume" in profile:
                profile["volume"] = level


def shift_level(cfg, delta):
    # Each level moves from where it is, so a session override keeps its offset.
    cfg["volume"] = clamp(cfg.get("volume", 100) + delta)
    profiles = cfg.get("sessionVoices", {})
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict) and "volume" in profile:
                profile["volume"] = clamp(profile["volume"] + delta)


def settled(observed, expected):
    return all(observed.get(name) == values for name, values in expected.items())


def volume(config, value="", request_id=None, scope="both", addressed="claude"):
    """Apply one level change. value is "up", "down", a whole number (clamped to 0-100), or
    empty for a reading. A repeated request_id returns its first receipt and applies nothing."""
    if scope not in SCOPES:
        raise ValueError("Scope must be self or both")
    paths = sources(config)
    if addressed not in paths:
        raise ValueError(f"No {addressed} speech profile is configured")
    names = [addressed] if scope == "self" else list(paths)
    if not config.get("voice_control_state_dir"):
        raise ValueError("voice_control_state_dir is not configured")
    # Every installed copy uses the same configured journal/lock directory.
    state = Path(config["voice_control_state_dir"]).resolve()
    request_id = request_id or uuid.uuid4().hex
    request = {"value": value, "scope": scope, "addressed": addressed}
    receipt = state / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")
    with writer_lock(state / "writer.lock"):
        if not value:
            # A reading changes nothing, so it neither needs nor writes a receipt.
            before = levels(paths)
            return {**request, "status": "verified", "levels": {n: before[n][0] for n in names},
                    "level": before[addressed][0], "observed": before, "changed": False}
        if receipt.exists():
            previous = read(receipt)
            # Receipts written before scopes existed changed both profiles for Claude.
            seen = {"value": previous.get("value"), "scope": previous.get("scope", "both"),
                    "addressed": previous.get("addressed", "claude")}
            if seen != request:
                raise ValueError("Request ID was already used for a different voice-level change")
            if previous["status"] == "applying":
                previous["observed"] = levels(paths)
                done = (settled(previous["observed"], previous["expected"]) if "expected" in previous
                        else all_at(previous["observed"], previous["target"]))
                previous["status"] = "verified" if done else "partial"
                write(receipt, previous)
            return {**previous, "replayed": True}
        before = levels(paths)
        if value in ("up", "down"):
            delta = step_size(config) * (1 if value == "up" else -1)
            change = lambda cfg: shift_level(cfg, delta)
        elif value.isascii() and value.isdigit():
            level = clamp(int(value))
            change = lambda cfg: set_level(cfg, level)
        else:
            raise ValueError("Voice level must be up, down, or a whole number")
        expected = {}
        for name in names:
            cfg = read(paths[name])
            change(cfg)
            expected[name] = file_levels(cfg)
        result = {"request_id": request_id, **request, "status": "applying", "expected": expected,
                  "levels": {name: values[0] for name, values in expected.items()},
                  "before": before, "changed": True}
        if len(set(result["levels"].values())) == 1:
            result["target"] = next(iter(result["levels"].values()))
        write(receipt, result)
        try:
            # Two writes are not a transaction. Verify every write and keep any partial outcome.
            for name in names:
                original = paths[name].read_bytes()
                cfg = json.loads(original.decode("utf-8-sig"))
                change(cfg)
                write(paths[name], cfg, expected=original)
            result["observed"] = levels(paths)
            result["status"] = "verified" if settled(result["observed"], expected) else "partial"
        except (OSError, ValueError, RuntimeError) as error:
            result["status"] = "partial"
            result["error"] = type(error).__name__
            try:
                result["observed"] = levels(paths)
            except (OSError, ValueError):
                result["observed"] = None
        write(receipt, result)
        return result


def confirmation(result):
    """Exactly one sentence: "Level 40." for one voice, "Both at 40." when both match."""
    levels_now = result.get("levels") or {}
    if result.get("scope") == "self" or len(levels_now) == 1:
        return f"Level {next(iter(levels_now.values()))}."
    if len(set(levels_now.values())) == 1:
        return f"Both at {next(iter(levels_now.values()))}."
    return f"Claude at {levels_now.get('claude')}, Codex at {levels_now.get('codex')}."


def hook(config, arguments, addressed="claude", contribution=None):
    """Run the speech hook in the addressed assistant's voice. The caller's floor stamp is
    inherited from the environment; a named contribution replaces the inherited one."""
    if addressed == "codex":
        session = "codex/" + config["codex_thread_id"] if config.get("codex_thread_id") else "voice-control"
        environment = {**os.environ, "LUGOS_SPEECH_CONFIG": config["speech_config"],
                       "LUGOS_SPEECH_SESSION": session}
    else:
        environment = {**os.environ, "LUGOS_SPEECH_CONFIG": config["shared_speech_config"],
                       "LUGOS_SPEECH_SESSION": "voice-control"}
    if contribution:
        environment["LUGOS_SPEECH_CONTRIBUTION"] = str(contribution)
    return subprocess.run([config["node"], config["speech_hook"], *arguments], env=environment,
                          capture_output=True, text=True, timeout=60, creationflags=HIDDEN)


def dispatch(config, control, value="", scope="both", contribution_id=None, addressed="claude", speak=False):
    """One typed control. Returns the receipt plus applied and confirmation. Only volume,
    status and stop are handled here; recall and quiet keep their own paths."""
    if control == "stop":
        # Stop does not wait for the volume writer lock or speak a confirmation.
        completed = hook(config, ["stop"])
        return {"status": "stop_requested" if completed.returncode == 0 else "failed",
                "applied": completed.returncode == 0, "confirmation": None}
    if control == "status":
        value = ""
    elif control != "volume":
        raise ValueError(f"The dispatcher does not handle {control}")
    result = volume(config, value, contribution_id, scope, addressed)
    fresh = result["status"] == "verified" and not result.get("replayed")
    result["applied"] = fresh and result.get("changed", False)
    result["confirmation"] = confirmation(result) if fresh else None
    if speak and result["confirmation"]:
        spoken_level = result["levels"].get(addressed, next(iter(result["levels"].values())))
        muted = addressed == "codex" and read(config["speech_config"]).get("masqMuted", False)
        if not spoken_level or muted:
            result["feedback"] = "silent_at_zero" if not spoken_level else "muted"
        else:
            try:
                feedback = hook(config, ["say", result["confirmation"]], addressed, contribution_id)
                result["feedback_exit_code"] = feedback.returncode
            except (OSError, subprocess.SubprocessError) as error:
                # A feedback timeout does not undo verified settings or authorize a retry.
                result["feedback"] = "unconfirmed"
                result["feedback_error"] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get(
        "LUGOS_VOICE_EXCHANGE_CONFIG", str(Path(__file__).with_name("voice-exchange.json"))))
    parser.add_argument("action", choices=["volume", "status", "stop"])
    parser.add_argument("value", nargs="?", default="")
    parser.add_argument("--request-id")
    parser.add_argument("--scope", choices=SCOPES, default="both")
    parser.add_argument("--addressed", choices=["claude", "codex"], default="claude")
    parser.add_argument("--speak", action="store_true")
    args = parser.parse_args()
    try:
        result = dispatch(read(args.config), args.action, args.value, args.scope,
                          args.request_id, args.addressed, args.speak)
        print(json.dumps(result))
        return 0 if result["status"] in ("verified", "stop_requested") else 1
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
