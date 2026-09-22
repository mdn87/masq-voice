"""One local voice-level control for the two configured speech profiles."""
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
    paths = {"claude": Path(config["shared_speech_config"]).resolve(),
             "codex": Path(config["speech_config"]).resolve()}
    if paths["claude"] == paths["codex"]:
        raise ValueError("The two voice profiles must have separate files")
    return paths


def levels(paths):
    result = {}
    for name, path in paths.items():
        cfg = read(path)
        values = [cfg.get("volume", 100)]
        profiles = cfg.get("sessionVoices", {})
        if isinstance(profiles, dict):
            values += [p["volume"] for p in profiles.values()
                       if isinstance(p, dict) and "volume" in p]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100 for v in values):
            raise ValueError("Invalid configured voice level")
        result[name] = values
    return result


def all_at(observed, level):
    return all(value == level for values in observed.values() for value in values)


def set_level(cfg, level):
    cfg["volume"] = level
    profiles = cfg.get("sessionVoices", {})
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict) and "volume" in profile:
                profile["volume"] = level


def volume(config, value="", request_id=None):
    paths = sources(config)
    # Every installed copy uses the same configured journal/lock directory.
    state = Path(config["voice_control_state_dir"]).resolve()
    request_id = request_id or uuid.uuid4().hex
    receipt = state / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")
    with writer_lock(state / "writer.lock"):
        if receipt.exists():
            previous = read(receipt)
            if previous.get("value") != value:
                raise ValueError("Request ID was already used for a different voice-level change")
            if previous["status"] == "applying":
                previous["observed"] = levels(paths)
                previous["status"] = "verified" if all_at(previous["observed"], previous["target"]) else "partial"
                write(receipt, previous)
            return {**previous, "replayed": True}
        before = levels(paths)
        current = before["claude"][0]
        if not value:
            return {"status": "verified" if all_at(before, current) else "mismatch", "level": current,
                    "observed": before, "changed": False}
        if value in ("up", "down"):
            if not all_at(before, current):
                raise ValueError("Voice levels disagree; set an explicit level before a relative change")
            target = max(0, min(100, int(current) + (5 if value == "up" else -5)))
        elif value.isdigit() and 0 <= int(value) <= 100:
            target = int(value)
        else:
            raise ValueError("Voice level must be up, down, or a number from 0 to 100")
        result = {"request_id": request_id, "value": value, "status": "applying", "target": target,
                  "before": before, "changed": True}
        write(receipt, result)
        try:
            # Two writes are not a transaction. Verify both and preserve any partial outcome.
            for path in paths.values():
                original = path.read_bytes()
                cfg = json.loads(original.decode("utf-8-sig"))
                set_level(cfg, target)
                write(path, cfg, expected=original)
            result["observed"] = levels(paths)
            result["status"] = "verified" if all_at(result["observed"], target) else "partial"
        except (OSError, ValueError, RuntimeError) as error:
            result["status"] = "partial"
            result["error"] = type(error).__name__
            try:
                result["observed"] = levels(paths)
            except (OSError, ValueError):
                result["observed"] = None
        write(receipt, result)
        return result


def hook(config, arguments):
    environment = {**os.environ, "LUGOS_SPEECH_CONFIG": config["shared_speech_config"],
                   "LUGOS_SPEECH_SESSION": "voice-control"}
    return subprocess.run([config["node"], config["speech_hook"], *arguments], env=environment,
                          capture_output=True, text=True, timeout=60, creationflags=HIDDEN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get(
        "LUGOS_VOICE_EXCHANGE_CONFIG", str(Path(__file__).with_name("voice-exchange.json"))))
    parser.add_argument("action", choices=["volume", "stop"])
    parser.add_argument("value", nargs="?", default="")
    parser.add_argument("--request-id")
    parser.add_argument("--speak", action="store_true")
    args = parser.parse_args()
    try:
        config = read(args.config)
        if args.action == "stop":
            # Stop does not wait for the volume writer lock or speak a confirmation.
            completed = hook(config, ["stop"])
            result = {"status": "stop_requested" if completed.returncode == 0 else "failed"}
        else:
            result = volume(config, args.value, args.request_id)
            if args.speak and result["status"] == "verified" and not result.get("replayed"):
                level = result.get("target", result.get("level"))
                if level:
                    try:
                        feedback = hook(config, ["say", f"Level {level}."])
                        result["feedback_exit_code"] = feedback.returncode
                    except (OSError, subprocess.SubprocessError) as error:
                        # A feedback timeout does not undo verified settings or authorize a retry.
                        result["feedback"] = "unconfirmed"
                        result["feedback_error"] = type(error).__name__
                else:
                    result["feedback"] = "silent_at_zero"
        print(json.dumps(result))
        return 0 if result["status"] in ("verified", "stop_requested") else 1
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
