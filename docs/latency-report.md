# Latency report

The latency report answers "how fast was that?": how long each stage took between the end of
your speech and the assistant's spoken reply.

## Asking for it by voice

Say the wake phrase, then any of:

- "latency report"
- "how fast was that"
- "how long did that take"
- "timing report"
- "latency check"

The listener runs `speak-response.mjs latency spoken` itself. No prompt is sent and no model
turn is used. It reads one line aloud: how long after Enter you heard the acknowledgement, the
opening sentence, the first tool call and the final `Spoken:` line, plus the average over
recent turns.

## Asking for the full table

```bash
node <HOOKS_DIR>/speak-response.mjs latency [N] [spoken] [transcript.jsonl]
```

This prints the last N turns (default 10) of the most recently active session.

| Column | Meaning |
|---|---|
| `silence->enter` | End of speech to Enter: transcription plus any triage. |
| `enter->prm` | Enter to the prompt landing in the assistant. |
| `heard` | The acknowledgement audio starts. |
| `1st text` | First text from the assistant. |
| `plan` | Audio of the opening sentence starts. |
| `1st tool` | First tool call. |
| `spoken` | Audio of the final `Spoken:` line starts. |
| `turn` | The turn ends (Stop). |

A message sent while the assistant is mid-turn is listed under that turn with `seen` (from
submission until the assistant actually receives it, which happens with the next tool result),
`answer text` and `spoken`. A column shows `-` where the trail has no entry.

## The trail behind the report

Every stage appends one line to `latency.log` in the speech hook's temp folder
(`<system temp>/claude-tts/latency.log`):

```
<epoch milliseconds> TAB <event> TAB <detail>
```

The file is trimmed to its last 5000 lines once it passes 2 MB.

| Written by | Events |
|---|---|
| Listener | `wake-heard`, `cmd-end`, `utterance-end`, `transcribed`, `triage-done`, `sent`, `alias-run`, `sound-check` |
| Assistant hooks | `prompt`, `prompt-midturn`, `tool`, `ack-launch`, `plan-launch`, `stop-launch`, `stop`, `voice-selected` |
| Speaker script | `audio-start`, `audio-end`, each tagged `ack`, `plan`, `stop` or `say` |

## Reading the trail when something is wrong

The trail is the fastest way to tell which half of the system failed:

- **`wake-heard` and `cmd-end` appear, but no `prompt` follows.** The microphone side works
  and delivery failed. Read the delivery receipt (see `operations.md`).
- **`stop-launch` appears with no `audio-start` after it.** The speech job hung before it made
  sound. While it hangs, the listener stays muted (see `operations.md`).
- **No listener events at all for a stretch.** Either nobody spoke, the microphone dropped, or
  the listener was muted by a speech job that never finished.
- **`cmd-end` shows a fragment of what you said.** The silence timer ended the command early,
  usually because of a pause mid-sentence.
