"""System/behaviour tests for terminal_runtime_service.py.

These tests import the service module directly and exercise the platform-
independent logic (encoding, mode tracking, state detection, locks).  They do
not start a real PTY and therefore run on any platform.
"""

from __future__ import annotations

import pytest

from scripts.terminal_runtime_service import (
    CreateSessionRequest,
    TerminalSession,
    ctrl_key,
    is_dangerous_text,
)


# -----------------------------------------------------------------------------
# Encoding helpers
# -----------------------------------------------------------------------------

def test_ctrl_key_basic():
    assert ctrl_key("CTRL_C") == b"\x03"
    assert ctrl_key("ctrl-a") == b"\x01"
    assert ctrl_key("CTRL_Z") == b"\x1a"


def test_ctrl_key_invalid():
    with pytest.raises(ValueError):
        ctrl_key("CTRL")
    with pytest.raises(ValueError):
        ctrl_key("CTRL_1")


def test_encode_text_action():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    action = s._encode_action.__self__  # noqa: SLF001
    # Use the public-ish method directly via the instance.
    from scripts.terminal_runtime_service import TerminalAction

    assert s._encode_action(TerminalAction(type="text", text="hello")) == "hello"  # noqa: SLF001


def test_encode_submit_action():
    from scripts.terminal_runtime_service import TerminalAction

    s = TerminalSession(CreateSessionRequest(command="bash"))
    assert s._encode_action(TerminalAction(type="submit", text="ls")) == "ls\r"  # noqa: SLF001


def test_encode_paste_without_bracketed_paste():
    from scripts.terminal_runtime_service import TerminalAction

    s = TerminalSession(CreateSessionRequest(command="bash"))
    assert s._encode_action(TerminalAction(type="paste", text="hello")) == "hello"  # noqa: SLF001


def test_encode_paste_with_bracketed_paste():
    from scripts.terminal_runtime_service import TerminalAction

    s = TerminalSession(CreateSessionRequest(command="bash"))
    s.modes.bracketed_paste = True
    assert s._encode_action(TerminalAction(type="paste", text="hello")) == "\x1b[200~hello\x1b[201~"  # noqa: SLF001


def test_encode_key_arrows():
    from scripts.terminal_runtime_service import TerminalAction

    s = TerminalSession(CreateSessionRequest(command="bash"))
    assert s._encode_action(TerminalAction(type="key", key="UP")) == "\x1b[A"  # noqa: SLF001
    s.modes.application_cursor_keys = True
    assert s._encode_action(TerminalAction(type="key", key="UP")) == "\x1bOA"  # noqa: SLF001


def test_encode_key_control():
    from scripts.terminal_runtime_service import TerminalAction

    s = TerminalSession(CreateSessionRequest(command="bash"))
    assert s._encode_action(TerminalAction(type="control", key="CTRL_D")) == "\x04"  # noqa: SLF001


# -----------------------------------------------------------------------------
# Security helpers
# -----------------------------------------------------------------------------

def test_is_dangerous_text_detects_sudo():
    assert is_dangerous_text("sudo rm -rf /") is not None


def test_is_dangerous_text_allows_safe():
    assert is_dangerous_text("ls -la") is None


# -----------------------------------------------------------------------------
# Mode tracking
# -----------------------------------------------------------------------------

def test_track_application_cursor_keys():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    changed = s._track_modes("\x1b[?1h")  # noqa: SLF001
    assert ("application_cursor_keys", True) in changed
    assert s.modes.application_cursor_keys is True


def test_track_bracketed_paste():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    changed = s._track_modes("\x1b[?2004h")  # noqa: SLF001
    assert ("bracketed_paste", True) in changed


def test_track_no_duplicate_events():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    s._track_modes("\x1b[?1h")  # noqa: SLF001
    changed = s._track_modes("\x1b[?1h")  # noqa: SLF001
    assert changed == []


# -----------------------------------------------------------------------------
# State detection
# -----------------------------------------------------------------------------

def test_detect_shell_prompt():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    text = "user@host:~$"
    lines = [text]
    detected = s._detected_state_locked(text, lines, 1000)  # noqa: SLF001
    assert detected["prompt_likely"] is True
    assert detected["shell_prompt_likely"] is True
    assert detected["input_readiness"]["status"] == "likely_ready"


def test_detect_confirmation():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    text = "Are you sure? [Y/n]"
    detected = s._detected_state_locked(text, [text], 1000)  # noqa: SLF001
    assert detected["confirmation_likely"] is True


def test_detect_error():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    text = "Traceback (most recent call last): error happened"
    detected = s._detected_state_locked(text, [text], 0)  # noqa: SLF001
    assert detected["error_likely"] is True


def test_detect_tui():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    s.modes.alternate_screen = True
    detected = s._detected_state_locked("menu", ["menu"], 1000)  # noqa: SLF001
    assert detected["tui_likely"] is True


# -----------------------------------------------------------------------------
# Locks
# -----------------------------------------------------------------------------

def test_acquire_and_release_lock():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    result = s.acquire_lock("alice", lease_ms=10000)
    assert result["ok"] is True
    assert s.session_lock.active() is True

    token = result["lock_token"]
    result = s.release_lock("alice", token)
    assert result["released"] is True
    assert s.session_lock.active() is False


def test_other_actor_blocked_without_force():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    s.acquire_lock("alice", lease_ms=10000)
    with pytest.raises(PermissionError):
        s.acquire_lock("bob", lease_ms=10000)


def test_force_take_lock():
    s = TerminalSession(CreateSessionRequest(command="bash"))
    s.acquire_lock("alice", lease_ms=10000)
    result = s.acquire_lock("bob", lease_ms=10000, force=True)
    assert result["ok"] is True
    assert s.session_lock.actor == "bob"
