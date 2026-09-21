# masq-voice

Public repository. Spoken-command vocabulary and operating notes for a voice-driven AI coding assistant.

## Rules for agents working here

- This repository is public. Never commit absolute paths from a real machine, usernames, hostnames, IP addresses, session ids, thread ids, tokens, or the contents of any token file.
- `aliases.example.json` is the source of truth here and uses the placeholders `<HOOKS_DIR>`, `<LISTENER_DIR>` and `<PYTHON>`. A live `aliases.json` is ignored by git on purpose.
- `docs/voice-commands.md` is generated. Change `aliases.example.json`, then run `python scripts/gen_commands.py`.
- In `docs/operations.md`, every cause states how it was established. Write "not proven" when it was not.
- Keep approval and control words out of any text that a speech hook will read aloud; an open microphone hears it.
