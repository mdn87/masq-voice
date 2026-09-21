# Operations runbook

Faults that have actually happened, with the cause and the fix. Each entry says how the cause
was established, because a guess recorded as a fact wastes the next debugging session.

Paths are written relative to three folders: the listener folder, the guardian state folder
and the speech hook's temp folder (`<system temp>/claude-tts`).

## First look

1. Guardian `status.json`: `state`, `reason`, and the embedded listener health (`ready`,
   `mic_age`, `audio_peak`, `stage`, `tts_age`, `last_delivery`).
2. The newest delivery receipt in the voice-exchange `turns` folder. Each receipt is a JSON
   file named by the SHA-256 of the contribution id. Read `delivery`, `state` and `errors`.
3. The tail of `latency.log` (see `latency-report.md`).

`audio_peak` near zero with a fresh `mic_age` means the microphone is connected and silent,
which usually means a wireless transmitter is switched off.

## The cue plays, then no text is entered

**Cause (confirmed from the receipt):** delivery is pinned to one session. The voice-exchange
config names a `claude_session_title` and a `claude_transcript`. The listener looks for the
button "<title>, rename session" in the desktop app and refuses to paste anywhere else. In any
other session the receipt reads `"errors": {"claude": "Open the configured Claude voice task"}`
and the guardian reports `attention: last command delivery is unconfirmed`.

This is a safeguard, not a microphone problem. Adjusting input levels does nothing.

**Fix:** give the target session a distinct title, set `claude_session_title` and
`claude_transcript` to match it, and relaunch the guardian. Repeat whenever voice should land
in a different session. An auto-generated title such as "Hello" can collide with another
session, so rename it first.

Write the config with a JSON library. Do not write it with a shell heredoc: some shells
collapse backslashes, which corrupts Windows paths.

Delivery also requires exactly one app window, exactly one message box, and an empty message
box. An unsent draft blocks delivery with `Claude composer contains an unsent draft`.

## Heard but not delivered: `Claude target changed before delivery`

**Cause: not proven.** The error is raised when, after the listener asks for focus, the message
box still lacks keyboard focus, or the session title or empty-draft check fails. The likely
cause is the operating system refusing a focus change while another application is in front.
The same sentence repeated a few seconds later was delivered.

**Next time:** record which window is in the foreground at the moment of failure.

## The listener goes deaf after the assistant replies

**Cause (mechanism confirmed in code, release moment not observed):** the speech job for a
reply hung. Its audio file stayed at 0 bytes, the text-to-speech processes and the speaker
process stayed alive for over 100 seconds, and `latency.log` showed `stop-launch` with no
`audio-start`. The listener mutes itself while the speech floor has an active speaker, and the
floor is held until both the speech worker and its child process exit. A hung speech job
therefore leaves the listener muted. No listener events were logged for about two minutes.

**Fix:**

```bash
node <HOOKS_DIR>/speak-response.mjs stop
```

This releases the floor and ends the speaker processes. Afterwards the processes and the
`floor-active-*.json` file for that job were gone, and the next wake phrase was heard.

**Still open:** the hook sets no timeout on the text-to-speech call, so this can happen again.

## The cue tone is painfully loud

**Cause (confirmed):** the tone was played with `winsound.Beep`, which has no volume control
and plays at full scale.

**Fix:** render the tone as a sine wave in memory, scale it, and play it with
`winsound.PlaySound(data, winsound.SND_MEMORY)`. The listener flag `--beep-volume` sets the
loudness as a fraction of full scale; `0` silences the cues. `0.12` was still too loud on a
headset; `0.03` is the current value.

**Why it never balances against the spoken voice (likely, not proven):** the tone plays on the
system default output device, while spoken replies go to a named output device with their own
volume setting. A mixer slider for one does not move the other. Playing the tone on the same
device as the speech would put both under one control.

## Do we need the cue at all?

No part of delivery depends on it. The wake cue is the only immediate sign that the wake phrase
registered; without it a missed wake phrase and a heard one sound identical until the
acknowledgement plays. The "sent" cue duplicates the spoken acknowledgement and can go.

## Relaunching the listener

Use the guardian, not a process kill: `disable`, wait for `state: disabled` in `status.json`,
then `enable`. The listener is healthy again within a few seconds.

The guardian runs the listener inside its own child process and passes the configured
arguments in at startup, so listener flags never show on any process command line. Confirm a
flag took effect by the listener starting cleanly; an unknown flag stops it at launch.

## Other observations, cause not examined

- A very high guardian restart count with runs of `no input device matching <name>` and
  `microphone callbacks stopped`. A wireless microphone receiver dropping off USB is the
  likely cause.
- Commands arriving as fragments after the wake phrase. The silence timer probably ends the
  command during a pause.
- The speech floor service answers POST only. Read floor state from the `floor-active-*.json`
  files instead of querying the service.
