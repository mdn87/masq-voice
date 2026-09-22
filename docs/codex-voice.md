# Voice controls for Codex

Say **"hey Codex, [request], end turn"** to address the pinned Codex desktop task.
Say **"hey Claude, [request], end turn"** to address the configured Claude task.
The adapter selects only the addressed assistant. Changing the foreground window
does not change the destination. A neutral wake phrase uses Codex by default.

Codex replies in brief, plain, conversational English. The voice context asks it to
speak one short summary through the existing speech-floor helper. Written replies
can retain details needed for review. No herald persona or extra approval gate is
enabled. Existing task permissions still apply.

## Controls

Use the same aliases in [voice-commands.md](voice-commands.md), preceded by "hey Codex":

| Say after the wake phrase | Effect |
|---|---|
| "voice quieter", "volume down", "quieter", "turn it down" | Lower both voices by five points, bounded at zero. |
| "voice louder", "volume up", "louder", "speak up" | Raise both voices by five points, bounded at 100. |
| "voice level" | Read the shared level after checking both profiles. |
| "voice level five", "volume five" | Set both voices to five. |
| "mute", "voice off" | Stop current shared speech and silence future Codex replies. |
| "unmute", "voice on" | Enable Codex replies with its saved volume and voice. |
| "use Guy", "use Sonia", "use David", "list voices" | Select or list voices for Codex. |
| "stop speaking", "stop talking" | Stop current and queued speech and hold the floor quiet until the next user turn. It does not cancel agent work. |
| "are you still working", "status" | Read the pinned Codex task's current activity without a model turn. |
| "testing", "test command" | Acknowledge locally without sending a model prompt. |
| "sound check", "mic check" | Read the existing listener health report in Codex's voice. |
| "master status", "roll call" | Run the existing roll call and read its output in Codex's voice. Its session inventory remains separately configured. |
| "latency report" | Report the last recorded Codex delivery duration, excluding recognition and reply time. |
| "plain English mode", "normal mode" | Ask Codex to use brief, plain English. |

Both wake phrases use the shared level and Stop controls locally, without a model
turn. Level changes update both configurations and any existing session-level gain
overrides, then read them back. Voices and mute settings remain separate. If levels
disagree, set an explicit level before using relative changes. Repeated contribution
IDs cannot apply the same step twice. Partial writes are reported as partial; a
feedback timeout does not undo a verified gain change. Muting speech leaves microphone
input and task execution active.

Claude's `gate` controls do not change Codex permissions. When addressed to Codex,
they explain that distinction locally. Other persona aliases report Codex's plain
speaking style instead of sending a Claude slash command to the wrong app.
Custom local aliases outside the known speech helpers retain their original behavior.

## Install into an existing listener

This repository supplies an adapter, not a microphone listener. It requires an
existing Windows `voice-send` installation with `_voice_exchange.py`, the speech
floor, guardian, and speech hook already working. Setup preserves the installed
listener's existing changes and rejects unfamiliar integration boundaries.

1. Identify the exact existing Codex task ID and verify that the desktop app can
   read it. Choose a task intended to receive spoken requests.
2. Preview with the existing listener directory and a working speech configuration:

   ```powershell
   python scripts/setup_codex.py --listener-dir <LISTENER_DIR> --thread-id <CODEX_TASK_ID> --speech-source <TTS_CONFIG>
   ```

3. Disable the listener through its guardian and wait for `state: disabled`:

   ```powershell
   <PYTHON> <LISTENER_DIR>/voice_guardian.py --config <GUARDIAN_CONFIG> disable
   <PYTHON> <LISTENER_DIR>/voice_guardian.py --config <GUARDIAN_CONFIG> status
   ```

4. Repeat the setup command with `--apply`. It backs up every existing target in
   `<LISTENER_DIR>/.local/masq-voice/backups/` before writing anything.
5. Verify the guardian's existing `--wake` list includes `hey codex` and `hey codecs`
   alongside the Claude phrases. Retain the chosen silence and microphone settings.
   If the listener supports `--beep-volume`, `0.03` is a quiet starting point and
   `0` disables cues. Setup does not change guardian arguments or mixer sliders.
6. Enable the guardian and verify its listener health reports `ready: true`:

   ```powershell
   <PYTHON> <LISTENER_DIR>/voice_guardian.py --config <GUARDIAN_CONFIG> enable
   <PYTHON> <LISTENER_DIR>/voice_guardian.py --config <GUARDIAN_CONFIG> status
   ```

7. Say "hey Codex, testing, end turn" for a local control check. Then ask a short
   question to test task delivery and the spoken reply. Verify that the receipt
   names Codex and records `accepted`; that proves submission, not completion.

Setup writes `_masq_voice.py` and `_voice_controls.py`, adds the exact control phrases
to an existing alias file, patches the listener's alias selection and the
exchange's Codex speech path, and updates `voice-exchange.json`. It pins the task,
disables observer copies, and creates `.local/masq-voice/codex-tts.json`. The source
TTS config is read only during setup: selected audio fields, the previous Codex
voice profile, and explicit `speechFloor` URL/token-file/host references are copied.
The token contents and unrelated service credentials are never copied. If no previous profile
matches, the default is Guy with David as the native fallback. Reinstallation
preserves existing Codex volume, mute state, voice, and output device, and refreshes
the speaking-floor references. Set a common initial level explicitly after installation:

```powershell
python <LISTENER_DIR>/_voice_controls.py volume 5
```

The two profiles must name the intended output device. This installer does not
change Windows defaults or Sonar channels. The installed speech-floor hook must
support managed playback for both profiles. Stamped speech without a configured
floor must fail quietly instead of taking the unmanaged playback path.

Replies longer than 500 characters end with a notice that the rest is in writing.
The original turn is claimed once before playback; a timeout never triggers an
automatic replay. The floor worker's `speech-results.jsonl` in the temporary
`claude-tts` directory distinguishes completed, rejected, interrupted, expired, and
failed requests. Its timestamps describe player processes, not acoustic onset;
`audibility: not_measured` requires a separate listening check.

The existing bundled Codex desktop transport is used for task delivery and status.
It is an installed-app integration and may need updating after a Codex app update.
No API key, new model service, or separate Codex CLI writer is involved. Spoken
summaries use the existing exchange helper rather than requiring new lifecycle hooks.
Codex also documents a separate [hook mechanism](https://learn.chatgpt.com/docs/hooks),
whose configuration and trust requirements are outside this adapter.

## Verification and recovery

```powershell
python -m unittest discover -s tests -v
python <LISTENER_DIR>/_masq_voice.py busy
python <LISTENER_DIR>/_voice_controls.py volume
```

The last two commands read status and saved gain without speaking or starting a
model turn. Automated tests cover routing, private controls, speech-floor metadata,
setup previews, backups, and repeat installation. A real spoken phrase is still
needed to verify recognition, delivery, and audible playback together.

To move voice to another task, disable the guardian and rerun setup with that exact
task ID, then enable it. To roll back, disable the guardian and restore the saved
`voice_send.py`, `_voice_exchange.py`, and `voice-exchange.json` from one backup
directory, then enable it. The extra adapter file can remain unused.

Keep live settings, task IDs, receipts, transcripts, and backup files out of this
public repository. Only placeholder examples and reusable code belong in Git.
