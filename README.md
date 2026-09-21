# masq-voice

Spoken commands for an AI coding assistant running in a desktop app: what you can say, what
each phrase does, how to measure how fast it responds, and how to fix it when it goes quiet.

This repository is a seed. It holds the command vocabulary and the operating notes. It does
not hold the listener or the speech hook themselves; those are separate programs, described
under "What this talks to" below.

## What is here

| Path | What it is |
|---|---|
| `aliases.example.json` | The full phrase list: 123 spoken phrases mapped to 36 actions. Machine paths are replaced by placeholders. |
| `docs/voice-commands.md` | The same list as a readable table, generated from the JSON. |
| `docs/latency-report.md` | How to ask "how fast was that" and how to read the timing trail. |
| `docs/operations.md` | Runbook: faults that have actually happened, their causes, and the fix for each. |
| `scripts/gen_commands.py` | Regenerates `docs/voice-commands.md` from an aliases file. |

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
6. Prompts are pasted into the assistant's message box through the operating system's
   accessibility tree, then confirmed by finding the text in the session transcript. Delivery
   is pinned to one named session on purpose, so speech can never land in the wrong task.
7. When the assistant finishes, a stop hook reads its closing `Spoken:` line aloud. The
   listener mutes itself while that speech plays so it does not transcribe its own voice.

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
