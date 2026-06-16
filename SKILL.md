---
name: agent-terminal-runtime
description: Control a persistent virtual terminal session via observe, act, and wait. Use for interactive CLI/TUI programs or CLI agents.
---

# Agent Terminal Runtime

Use this skill when you need to control a **persistent virtual terminal session** through Agent Terminal Runtime.

This is not a one-shot command runner. Treat it as a live environment:

```text
observe current screen -> decide -> act -> wait -> observe again
```

The Runtime service is expected to be running, usually at:

```text
http://127.0.0.1:18650
```

The helper script is available at:

```text
{baseDir}/scripts/atr.py
```

It calls the Runtime HTTP API and returns JSON or plain text.

---

## Required operating loop

Always follow this loop:

1. Observe before acting.
2. If human takeover is active, stop acting.
3. If confirmation is likely, ask the user before confirming.
4. Use semantic actions only; do not send raw ANSI escape bytes.
5. After each action, wait for `screen_stable` or `input_likely_ready`.
6. Observe again before deciding the next step.

---

## Process lifecycle

ATR only monitors the process it directly starts. **Always create the session with the target program as the command** (e.g. `vim`, `kimi`, `fzf`, `python3 script.py`), not by starting `bash` and then running the program inside. Subprocesses inside a shell session are invisible to ATR.

---

More details and usages are in:
```text
{baseDir}/references/dialog.md
```
