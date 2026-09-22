# masq-voice

Talk to an AI coding assistant running in a desktop app. Voice is the main way in: you say a
wake phrase, then either a short command or an ordinary request, and the assistant answers
out loud. The keyboard is the fallback, not the default.

## Start here: what to say

Say the wake phrase, then the command: "hey claude, status". The wake phrase names the
assistant you are addressing, so "hey codex" sends your request to a different assistant.
A two-tone cue confirms the wake phrase was heard.

| You want to | Say | Reaches the assistant? |
|---|---|---|
| Ask for anything | the wake phrase, then your request in plain words | Yes, as a prompt |
| Know whether it is still working | "status", "are you done" | No |
| Cut off speech that is playing | "stop", "be quiet" | No |
| Change the shared speech volume | "voice louder", "voice quieter", "voice level five" | No |
| Turn spoken replies off or on | "mute", "unmute" | No |
| Change the speaking voice | "use sonia", "list voices" | No |
| Approve a plan the assistant proposed | "go ahead", "approved" | Yes, as `Go ahead.` |
| Halt a plan | "hold on", "stop the plan" | Yes, as a fixed halt message |
| Change your mind after waking it | "never mind", "cancel that" | No, nothing is sent |
| Change the writing style | "plain english mode", "persona off" | Yes, as a prompt for Codex or a slash command for Claude |
| Check the microphone | "sound check", "can you hear me" | No |
| Find out how fast the last turn was | "latency report" | No |

Commands marked "No" are run by the listener itself. They cost no tokens and they work while
the assistant is busy, which is the point: you can always stop it, quiet it, or ask what it is
doing without waiting for a turn.

The full list, 129 phrases for 38 actions, is in [docs/voice-commands.md](docs/voice-commands.md).

For **"hey Codex"**, install the [Codex voice adapter](docs/codex-voice.md) into the
existing listener. It routes requests to one pinned Codex desktop task, uses brief
plain-English spoken replies, and keeps Codex's voice and mute settings separate.
Both assistants use one local Voice level control and the configured speaking floor.
"Hey Claude" keeps its configured destination. The shared stop command interrupts
current and queued speech without cancelling agent work.

### How a turn sounds

1. You: the wake phrase. It: a two-tone cue.
2. You: the command or request, then "end turn" or a few seconds of silence.
3. It: reads back what it heard, when readback is switched on. If the readback would run
   past about 20 seconds it says the command was too long instead of reading part of it.
4. It: speaks one or two closing sentences when the work is done, ending with a fixed handoff
   cue so you know the floor is yours again.

### Speaking so that commands match

- Keep commands short and exact. "turn it down" matches. "can you turn yourself down just a
  little bit" does not reach the 85 % match threshold and goes to the assistant as an ordinary
  prompt instead, which is slower and costs a turn.
- Say a command on its own. A command buried inside a longer sentence is treated as a request.
- Anything that is not a command is a request. You do not need special wording for requests.

## What this repository is

It holds the command vocabulary, operating notes, and a Codex adapter for an existing
listener. It does not hold the listener or the speech hook themselves; those are separate
programs, described under "What this talks to" below.

## What is here

| Path | What it is |
|---|---|
| `aliases.example.json` | The full phrase list: 129 spoken phrases mapped to 38 actions. Machine paths are replaced by placeholders. |
| `docs/voice-commands.md` | The same list as a readable table, generated from the JSON. |
| `docs/latency-report.md` | How to ask "how fast was that" and how to read the timing trail. |
| `docs/operations.md` | Runbook: faults that have actually happened, their causes, and the fix for each. |
| `scripts/gen_commands.py` | Regenerates `docs/voice-commands.md` from an aliases file. |
| `integration/_masq_voice.py` | Routes known voice controls to Codex's private speech settings and reads its task status. |
| `integration/_voice_controls.py` | Serializes shared five-point level changes, verifies both profiles, and runs local Stop. |
| `scripts/setup_codex.py` | Previews or installs the adapter with backups, preserving existing listener changes. |
| `docs/codex-voice.md` | Codex controls, installation, verification, and rollback. |

## How a spoken command travels

1. A microphone listener runs all the time. It uses voice activity detection to cut speech
   into utterances and a local speech-to-text model to transcribe them.
2. It scans each transcript for a wake phrase such as "hey claude". A two-tone cue plays when
   the wake phrase is found, and the words after it become the command.
3. The command ends when you say an end phrase ("end turn") or stop talking for a few seconds.
4. The command is compared with `aliases.json`. A match needs 85 % similarity after
   punctuation is dropped, so short exact phrases match and long natural sentences do not.
5. A matched alias is either run locally by the listener (no model turn, no tokens) or
   replaced by exact text and sent as a prompt. Anything unmatched is sent as a prompt as is.
6. Claude prompts use its checked accessibility composer and transcript receipt. Codex
   prompts use the installed desktop app's task transport. Each assistant has a pinned
   destination; an uncertain delivery is recorded without automatic replay.
7. Claude's stop hook reads its closing `Spoken:` line. Codex's voice context asks it to
   speak one short summary through the exchange helper. Both use the speech floor, which
   pauses listening during playback and rejects speech superseded by a new contribution.

## Alias format

```json
{
  "plain english mode": "/masq:persona plain",
  "latency report": {
    "run": ["node", "<HOOKS_DIR>/speak-response.mjs", "latency", "spoken"],
    "speak_output": true
  }
}
```

- A string value is sent to the assistant as the prompt, replacing what you said. Use this for
  things that are awkward to say, such as slash commands.
- An object with `run` is executed by the listener itself and never reaches the assistant.
  Add `"speak_output": true` to have its standard output read aloud.

Placeholders in `aliases.example.json`:

| Placeholder | Replace with |
|---|---|
| `<HOOKS_DIR>` | The folder that holds the speech hook `speak-response.mjs`. |
| `<LISTENER_DIR>` | The folder that holds the listener, `sound_check.mjs` and `master_status.py`. |
| `<PYTHON>` | The Python interpreter of the listener's virtual environment. |

## Adding a command

1. Add the phrase to your live `aliases.json`. Add every wording you are likely to use; each
   wording is its own key.
2. Keep phrases short. "turn it down" matches. "can you turn yourself down just a little bit"
   does not reach 85 % and goes to the assistant as an ordinary prompt instead.
3. Restart the listener. Aliases are read at startup.
4. Copy the change into `aliases.example.json` with placeholders, then run
   `python scripts/gen_commands.py` to refresh the table.

## What this talks to

- **The listener**: a Python program (voice activity detection, local speech-to-text, wake
  phrase scan, alias matching, delivery into the desktop app) kept alive by a guardian process.
- **The speech hook**: `speak-response.mjs`, a stop hook for the assistant that speaks replies
  and also serves as the command-line tool behind most local aliases (`stop`, `volume`,
  `voice`, `busy`, `latency`, `gate`, `ping`).
- **The speech floor**: a small local service that decides who may make sound, so a person
  speaking and several assistant sessions do not talk over one another.

None of that code is in this repository yet.

## Safety notes for a public repository

- Never commit a live `aliases.json`; it contains absolute paths from your machine. It is
  listed in `.gitignore`. Commit `aliases.example.json` instead.
- Never commit tokens. The speech floor uses a token file; refer to it by description only.
- Approval words such as "go ahead" are aliases too. Anything a speaker plays into an open
  microphone can trigger them, including the assistant's own spoken replies. Keep approval and
  control words out of text that gets read aloud.
