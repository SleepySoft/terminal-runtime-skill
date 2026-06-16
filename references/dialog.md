## Tool helper usage

Use the local helper script with the `exec`/shell tool.

### Observe

```bash
python {baseDir}/scripts/atr.py observe --session-id bash-main
```

For debug view:

```bash
python {baseDir}/scripts/atr.py observe --session-id bash-main --view debug
```

### Screenshot

```bash
python {baseDir}/scripts/atr.py screenshot --session-id bash-main
```

### Submit text and Enter

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type submit --text 'echo hello && pwd && ls'
```

### Send a key

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type key --key DOWN
```

### Paste multi-line text

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type paste --text 'line1
line2
line3'
```

### Wait

```bash
python {baseDir}/scripts/atr.py wait --session-id bash-main --until screen_stable --timeout-ms 30000 --stable-ms 1000
```

### Lock

```bash
python {baseDir}/scripts/atr.py lock-acquire --session-id bash-main --actor openclaw --lease-ms 30000
```

Then pass the returned lock token to actions:

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --lock-token '<token>' --type key --key DOWN
```

---

## Process lifecycle

ATR only monitors the process it directly starts. **Always create a session with the target program as the command**, not via a shell wrapper:

| Approach | `process.pid` points to | `wait process_exit` waits for | Works? |
|----------|------------------------|------------------------------|--------|
| ✅ Direct: `vim`, `kimi`, `fzf` | The target program | The target program | Yes |
| ❌ Indirect: `bash` then run inside | `bash` | `bash` to exit | No (subprocess invisible) |

If you need an interactive shell, infer subprocess state from screen output (e.g., prompt returns).

---

## How to interpret observation

Focus on these fields:

```json
{
  "screen": {
    "visible_text": "current screen text",
    "stable_for_ms": 1200,
    "update_seq": 10
  },
  "input_modes": {
    "application_cursor_keys": true,
    "bracketed_paste": true,
    "alternate_screen": true
  },
  "detected_state": {
    "input_readiness": {
      "status": "likely_ready",
      "confidence": 0.8,
      "reasons": ["screen_stable", "prompt_detected"]
    },
    "shell_prompt_likely": true,
    "tui_likely": false,
    "menu_likely": false,
    "confirmation_likely": false,
    "error_likely": false
  },
  "control": {
    "human_takeover": false
  }
}
```

Also check `process` for health:

- `pid_exists`: real-time check via `os.kill(pid, 0)`. If `false` while `alive` is `true`, the process is a zombie or orphaned — recreate the session.
- `reader_alive`: if `false`, the internal reader thread has crashed and the screen is no longer updating — recreate the session.

`input_readiness` is probabilistic. If it is not `likely_ready`, wait or observe again instead of blindly typing.

---

## Shell prompt behavior

If `shell_prompt_likely=true`, the terminal is probably waiting at a shell prompt.

Use `submit` for shell commands:

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type submit --text 'pwd && ls'
```

Do not use `UP` / `DOWN` as menu navigation at a shell prompt; those keys operate shell history.

---

## TUI behavior

If `tui_likely=true` or `alternate_screen=true`, the terminal may be running a TUI program such as `dialog`, `fzf`, `vim`, `htop`, or `mc`.

Use semantic key actions:

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type key --key DOWN
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type key --key ENTER
```

Do not send raw escape bytes such as `\x1b[A` or `\x1bOB`. The Runtime tracks terminal modes and encodes keys correctly.

---

## CLI Agent behavior

When controlling another CLI agent:

1. Observe.
2. Confirm it is ready for input.
3. Send a bounded instruction, preferably asking it to plan before changing files.
4. Wait for screen stability.
5. Observe again.
6. Summarize progress to the user.
7. Stop on confirmation prompts or dangerous action blocks.

Example instruction:

```text
Please analyze the current project and propose a plan first. Do not modify files yet.
```

---

## Safety rules

You must stop and ask the user if:

- `detected_state.confirmation_likely=true`
- `control.human_takeover=true`
- an action result contains `requires_approval=true`
- a command involves deletion, overwrite, sudo, install, network pipe-to-shell, shutdown, reboot, or irreversible change

Never confirm destructive prompts by yourself.

---

## Standard sequence

```text
1. observe
2. if safe and ready, act
3. wait screen_stable
4. observe
5. summarize or continue
```

# Dialog test

Create a bash session in Agent Terminal Runtime, then submit:

```bash
dialog --menu "Choose an action" 15 50 5 1 "Show date" 2 "List files" 3 "Print working directory" 4 "Exit" 2>/tmp/dialog-result
```

Observe:

```bash
python {baseDir}/scripts/atr.py observe --session-id bash-main
```

Send DOWN:

```bash
python {baseDir}/scripts/atr.py act --session-id bash-main --actor openclaw --type key --key DOWN
```

Wait and observe:

```bash
python {baseDir}/scripts/atr.py wait --session-id bash-main --until screen_stable
python {baseDir}/scripts/atr.py observe --session-id bash-main
```
