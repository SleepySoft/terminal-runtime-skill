#!/usr/bin/env python3
"""
Agent Terminal Runtime - service-only production-oriented version.

Goal:
  A headless, persistent, AI-friendly terminal runtime. It is NOT a full terminal
  emulator; it focuses on correct observable state and correct action behavior.

Platform:
  Linux / macOS / WSL with POSIX PTY.

Install:
  python -m pip install fastapi uvicorn pyte pydantic

Run:
  python agent_terminal_runtime.py
  # or
  uvicorn agent_terminal_runtime:app --host 127.0.0.1 --port 18650

Core primitives:
  observe / act / wait / events

Important endpoints:
  GET    /health
  POST   /sessions
  GET    /sessions
  GET    /sessions/{id}/observe
  GET    /sessions/{id}/screenshot
  POST   /sessions/{id}/actions
  POST   /sessions/{id}/wait
  WS     /sessions/{id}/events
  POST   /sessions/{id}/locks/acquire
  POST   /sessions/{id}/locks/release
  POST   /sessions/{id}/takeover/start
  POST   /sessions/{id}/takeover/end
  GET    /sessions/{id}/audit
  GET    /sessions/{id}/logs/raw
  DELETE /sessions/{id}
"""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import queue
import re
import select
import shlex
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from pathlib import Path
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Literal, Optional, Union

import pyte
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field



# =============================================================================
# Configuration
# =============================================================================

HOST = os.environ.get("ATR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ATR_PORT", "18650"))
API_TOKEN = os.environ.get("ATR_API_TOKEN", "")  # if set, HTTP requires Bearer token
DEFAULT_MAX_SESSIONS = int(os.environ.get("ATR_MAX_SESSIONS", "32"))
DEFAULT_RAW_LOG_LIMIT = int(os.environ.get("ATR_RAW_LOG_LIMIT", "4000"))
DEFAULT_AUDIT_LIMIT = int(os.environ.get("ATR_AUDIT_LIMIT", "2000"))
DEFAULT_EVENT_LIMIT = int(os.environ.get("ATR_EVENT_LIMIT", "2000"))
DEFAULT_IDLE_TTL_SEC = int(os.environ.get("ATR_IDLE_TTL_SEC", "0"))  # 0 disables cleanup

BASE_DIR = Path(__file__).resolve().parent
UI_FILE = BASE_DIR / "terminal_debug_pro.html"

# =============================================================================
# Models
# =============================================================================

class SessionState(str, Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class CreateSessionRequest(BaseModel):
    id: Optional[str] = None
    command: Union[str, List[str]] = Field(..., description="Command string or argv list")
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    rows: int = Field(30, ge=5, le=300)
    cols: int = Field(100, ge=20, le=500)
    shell: bool = False
    owner: str = "default"
    purpose: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    idle_ttl_sec: Optional[int] = None


class TerminalAction(BaseModel):
    type: Literal["text", "submit", "paste", "key", "control", "resize"]
    text: Optional[str] = None
    key: Optional[str] = None
    modifiers: List[Literal["CTRL", "ALT", "SHIFT"]] = Field(default_factory=list)
    rows: Optional[int] = None
    cols: Optional[int] = None


class ActionRequest(BaseModel):
    actor: str = "anonymous"
    action: TerminalAction
    lock_token: Optional[str] = None
    allow_dangerous: bool = False


class WaitRequest(BaseModel):
    until: Literal[
        "screen_update",
        "screen_stable",
        "input_likely_ready",
        "process_exit",
        "any_activity",
    ] = "screen_stable"
    timeout_ms: int = Field(10000, ge=1, le=300000)
    stable_ms: int = Field(800, ge=50, le=60000)
    start_seq: Optional[int] = None


class LockAcquireRequest(BaseModel):
    actor: str
    lease_ms: int = Field(30000, ge=1000, le=3600000)
    force: bool = False


class LockReleaseRequest(BaseModel):
    actor: str
    lock_token: str


class TakeoverRequest(BaseModel):
    actor: str = "human"
    reason: Optional[str] = None


@dataclass
class TerminalEvent:
    type: str
    session_id: str
    ts: float
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "session_id": self.session_id, "ts": self.ts, "data": self.data}


@dataclass
class AuditEntry:
    ts: float
    session_id: str
    actor: str
    event: str
    detail: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "session_id": self.session_id,
            "actor": self.actor,
            "event": self.event,
            "detail": self.detail,
        }


@dataclass
class TerminalModes:
    application_cursor_keys: bool = False
    bracketed_paste: bool = False
    alternate_screen: bool = False
    mouse_reporting: bool = False
    focus_reporting: bool = False
    cursor_visible: bool = True

    def to_dict(self) -> Dict[str, bool]:
        return {
            "application_cursor_keys": self.application_cursor_keys,
            "bracketed_paste": self.bracketed_paste,
            "alternate_screen": self.alternate_screen,
            "mouse_reporting": self.mouse_reporting,
            "focus_reporting": self.focus_reporting,
            "cursor_visible": self.cursor_visible,
        }


@dataclass
class SessionLock:
    actor: Optional[str] = None
    token: Optional[str] = None
    lease_until: float = 0.0

    def active(self) -> bool:
        return bool(self.token and time.time() < self.lease_until)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active(),
            "actor": self.actor if self.active() else None,
            "lease_until": self.lease_until if self.active() else None,
        }


# =============================================================================
# Terminal encoding and detection helpers
# =============================================================================

NORMAL_CURSOR_KEYS = {
    "UP": "\x1b[A",
    "DOWN": "\x1b[B",
    "RIGHT": "\x1b[C",
    "LEFT": "\x1b[D",
}
APPLICATION_CURSOR_KEYS = {
    "UP": "\x1bOA",
    "DOWN": "\x1bOB",
    "RIGHT": "\x1bOC",
    "LEFT": "\x1bOD",
}
BASE_KEYS = {
    "ENTER": "\r",
    "RETURN": "\r",
    "TAB": "\t",
    "SHIFT_TAB": "\x1b[Z",
    "ESC": "\x1b",
    "ESCAPE": "\x1b",
    "BACKSPACE": "\x7f",
    "DELETE": "\x1b[3~",
    "INSERT": "\x1b[2~",
    "HOME": "\x1b[H",
    "END": "\x1b[F",
    "PAGEUP": "\x1b[5~",
    "PAGEDOWN": "\x1b[6~",
    "F1": "\x1bOP",
    "F2": "\x1bOQ",
    "F3": "\x1bOR",
    "F4": "\x1bOS",
    "F5": "\x1b[15~",
    "F6": "\x1b[17~",
    "F7": "\x1b[18~",
    "F8": "\x1b[19~",
    "F9": "\x1b[20~",
    "F10": "\x1b[21~",
    "F11": "\x1b[23~",
    "F12": "\x1b[24~",
}

PROMPT_PATTERNS = [
    re.compile(r"(^|\n).*[>$#]\s*$"),
    re.compile(r"(^|\n).*\?\s*$"),
    re.compile(r"(^|\n).*\[[yYnN]/[yYnN]\]\s*$"),
    re.compile(r"(^|\n).*(press enter|hit enter|continue).*", re.IGNORECASE),
    re.compile(r"(^|\n).*(请输入|输入|确认|继续).*[:：]?\s*$"),
]
CONFIRMATION_PATTERNS = [
    re.compile(r"(are you sure|continue|proceed|overwrite|delete|confirm).*\?", re.IGNORECASE),
    re.compile(r"\[[yYnN]/[yYnN]\]"),
    re.compile(r"(确认|是否继续|继续吗|覆盖|删除|确定).*"),
]
ERROR_PATTERNS = [
    re.compile(r"\b(error|exception|traceback|failed|failure|fatal)\b", re.IGNORECASE),
    re.compile(r"(错误|异常|失败|致命)"),
]
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+(/|\$|~|\*)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b"),
    re.compile(r"(curl|wget).*(\||>)\s*(sh|bash)"),
]

MODE_PATTERNS = [
    (re.compile(r"\x1b\[\?1h"), "application_cursor_keys", True),
    (re.compile(r"\x1b\[\?1l"), "application_cursor_keys", False),
    (re.compile(r"\x1b\[\?2004h"), "bracketed_paste", True),
    (re.compile(r"\x1b\[\?2004l"), "bracketed_paste", False),
    (re.compile(r"\x1b\[\?(?:47|1047|1049)h"), "alternate_screen", True),
    (re.compile(r"\x1b\[\?(?:47|1047|1049)l"), "alternate_screen", False),
    (re.compile(r"\x1b\[\?(?:1000|1002|1003|1006|1015)h"), "mouse_reporting", True),
    (re.compile(r"\x1b\[\?(?:1000|1002|1003|1006|1015)l"), "mouse_reporting", False),
    (re.compile(r"\x1b\[\?1004h"), "focus_reporting", True),
    (re.compile(r"\x1b\[\?1004l"), "focus_reporting", False),
    (re.compile(r"\x1b\[\?25h"), "cursor_visible", True),
    (re.compile(r"\x1b\[\?25l"), "cursor_visible", False),
]


def normalize_command(command: Union[str, List[str]], shell: bool) -> Union[str, List[str]]:
    if shell:
        return command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
    return command if isinstance(command, list) else shlex.split(command)


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def ctrl_key(name: str) -> bytes:
    n = name.upper().replace("CTRL_", "").replace("CTRL-", "")
    if len(n) != 1 or not ("A" <= n <= "Z"):
        raise ValueError(f"Unsupported control key: {name}")
    return bytes([ord(n) - ord("A") + 1])


def apply_alt(seq: str) -> str:
    return "\x1b" + seq


def is_dangerous_text(text: str) -> Optional[str]:
    for pat in DANGEROUS_PATTERNS:
        if pat.search(text):
            return pat.pattern
    return None


def now() -> float:
    return time.time()


# =============================================================================
# Terminal Session
# =============================================================================

class TerminalSession:
    def __init__(self, req: CreateSessionRequest):
        self.id = req.id or f"session-{uuid.uuid4().hex[:8]}"
        self.command = req.command
        self.cwd = req.cwd
        self.env = req.env or {}
        self.rows = req.rows
        self.cols = req.cols
        self.shell = req.shell
        self.owner = req.owner
        self.purpose = req.purpose
        self.tags = req.tags
        self.idle_ttl_sec = req.idle_ttl_sec if req.idle_ttl_sec is not None else DEFAULT_IDLE_TTL_SEC

        self.created_at = now()
        self.last_active_at = now()
        self.state = SessionState.CREATED
        self.master_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None

        self.screen = pyte.Screen(self.cols, self.rows)
        self.stream = pyte.Stream(self.screen)
        self.modes = TerminalModes()

        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.session_lock = SessionLock()
        self.human_takeover = False
        self.human_takeover_actor: Optional[str] = None
        self.human_takeover_reason: Optional[str] = None

        self.last_update_ts = 0.0
        self.update_seq = 0
        self.raw_chunks: Deque[str] = deque(maxlen=DEFAULT_RAW_LOG_LIMIT)
        self.events: Deque[TerminalEvent] = deque(maxlen=DEFAULT_EVENT_LIMIT)
        self.audit: Deque[AuditEntry] = deque(maxlen=DEFAULT_AUDIT_LIMIT)
        self.subscribers: List[queue.Queue] = []
        self.reader_thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            self.state = SessionState.STARTING
        master_fd, slave_fd = os.openpty()
        set_winsize(slave_fd, self.rows, self.cols)
        self.master_fd = master_fd
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        env = os.environ.copy()
        env.update(self.env)
        env.setdefault("TERM", "xterm-256color")
        env["COLUMNS"] = str(self.cols)
        env["LINES"] = str(self.rows)

        try:
            def _preexec_fn(fd):
                os.setsid()
                fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
            self.process = subprocess.Popen(
                normalize_command(self.command, self.shell),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd,
                env=env,
                shell=self.shell,
                preexec_fn=lambda: _preexec_fn(slave_fd),
                close_fds=True,
            )
            os.close(slave_fd)
        except Exception:
            try:
                os.close(slave_fd)
                os.close(master_fd)
            except Exception:
                pass
            with self.lock:
                self.state = SessionState.FAILED
            raise

        with self.lock:
            self.state = SessionState.RUNNING
            self.last_active_at = now()
        self._audit("system", "session_started", {"pid": self.process.pid, "command": self.command})
        self._emit("session_started", {"pid": self.process.pid, "command": self.command})
        self.reader_thread = threading.Thread(target=self._reader_loop, name=f"atr-reader-{self.id}", daemon=True)
        self.reader_thread.start()

    def stop(self, actor: str = "system") -> None:
        with self.lock:
            self.state = SessionState.STOPPING
        self._audit(actor, "session_stopping", {})
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except Exception:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except Exception:
                    self.process.kill()
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        with self.lock:
            self.state = SessionState.STOPPED
        self._emit("session_stopped", {})
        self._audit(actor, "session_stopped", {})

    # ---------------------------------------------------------------------
    # Reader and mode tracking
    # ---------------------------------------------------------------------
    def _reader_loop(self) -> None:
        assert self.master_fd is not None
        fd = self.master_fd
        while True:
            if self.process and self.process.poll() is not None:
                self._drain_once()
                with self.lock:
                    self.state = SessionState.EXITED
                    self.last_active_at = now()
                    code = self.process.returncode
                self._emit("process_exited", {"returncode": code})
                self._audit("system", "process_exited", {"returncode": code})
                return
            try:
                readable, _, _ = select.select([fd], [], [], 0.1)
            except (OSError, ValueError):
                return
            if readable:
                self._drain_once()

    def _drain_once(self) -> None:
        assert self.master_fd is not None
        while True:
            try:
                data = os.read(self.master_fd, 8192)
            except BlockingIOError:
                break
            except OSError as e:
                if e.errno in (errno.EIO, errno.EBADF):
                    break
                raise
            if not data:
                break
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = data.decode("latin-1", errors="replace")
            changed_modes = self._track_modes(text)
            with self.cond:
                self.raw_chunks.append(text)
                try:
                    self.stream.feed(text)
                except TypeError:
                    # pyte Screen may not support some CSI sequences (e.g. private=True)
                    # feed raw data but skip stream parsing for this chunk
                    pass
                self.last_update_ts = now()
                self.last_active_at = now()
                self.update_seq += 1
                seq = self.update_seq
                self.cond.notify_all()
            self._emit("screen_updated", {"seq": seq, "bytes": len(data)})
            for mode_name, value in changed_modes:
                self._emit("input_mode_changed", {"mode": mode_name, "value": value})

    def _track_modes(self, text: str) -> List[tuple[str, bool]]:
        changed: List[tuple[str, bool]] = []
        with self.lock:
            for pat, attr, value in MODE_PATTERNS:
                if pat.search(text):
                    old = getattr(self.modes, attr)
                    if old != value:
                        setattr(self.modes, attr, value)
                        changed.append((attr, value))
        return changed

    # ---------------------------------------------------------------------
    # Events and audit
    # ---------------------------------------------------------------------
    def _emit(self, typ: str, data: Dict[str, Any]) -> None:
        event = TerminalEvent(typ, self.id, now(), data)
        with self.cond:
            self.events.append(event)
            for q in list(self.subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
            self.cond.notify_all()

    def _audit(self, actor: str, event: str, detail: Dict[str, Any]) -> None:
        entry = AuditEntry(now(), self.id, actor, event, detail)
        with self.lock:
            self.audit.append(entry)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=300)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    # ---------------------------------------------------------------------
    # Locking / takeover
    # ---------------------------------------------------------------------
    def acquire_lock(self, actor: str, lease_ms: int, force: bool = False) -> Dict[str, Any]:
        with self.lock:
            if self.session_lock.active() and not force and self.session_lock.actor != actor:
                raise PermissionError(f"session locked by {self.session_lock.actor}")
            token = uuid.uuid4().hex
            self.session_lock = SessionLock(actor=actor, token=token, lease_until=now() + lease_ms / 1000.0)
        self._audit(actor, "lock_acquired", {"lease_ms": lease_ms, "force": force})
        self._emit("lock_acquired", {"actor": actor, "lease_until": self.session_lock.lease_until})
        return {"ok": True, "lock_token": token, "lease_until": self.session_lock.lease_until}

    def release_lock(self, actor: str, token: str) -> Dict[str, Any]:
        with self.lock:
            if not self.session_lock.active():
                return {"ok": True, "released": False, "reason": "no_active_lock"}
            if self.session_lock.token != token and self.session_lock.actor != actor:
                raise PermissionError("invalid lock token")
            self.session_lock = SessionLock()
        self._audit(actor, "lock_released", {})
        self._emit("lock_released", {"actor": actor})
        return {"ok": True, "released": True}

    def start_takeover(self, actor: str, reason: Optional[str]) -> Dict[str, Any]:
        with self.lock:
            self.human_takeover = True
            self.human_takeover_actor = actor
            self.human_takeover_reason = reason
        self._audit(actor, "human_takeover_started", {"reason": reason})
        self._emit("human_takeover_started", {"actor": actor, "reason": reason})
        return {"ok": True}

    def end_takeover(self, actor: str) -> Dict[str, Any]:
        with self.lock:
            self.human_takeover = False
            self.human_takeover_actor = None
            self.human_takeover_reason = None
        self._audit(actor, "human_takeover_ended", {})
        self._emit("human_takeover_ended", {"actor": actor})
        return {"ok": True}

    def _authorize_action(self, actor: str, token: Optional[str]) -> None:
        with self.lock:
            if self.human_takeover and actor != self.human_takeover_actor:
                raise PermissionError(f"human takeover active by {self.human_takeover_actor}")
            if self.session_lock.active():
                if self.session_lock.actor != actor and self.session_lock.token != token:
                    raise PermissionError(f"session locked by {self.session_lock.actor}")

    # ---------------------------------------------------------------------
    # Actions
    # ---------------------------------------------------------------------
    def write(self, data: Union[str, bytes]) -> None:
        if self.state not in (SessionState.RUNNING,) or self.master_fd is None:
            raise RuntimeError("session is not running")
        if isinstance(data, str):
            data = data.encode("utf-8")
        os.write(self.master_fd, data)
        with self.lock:
            self.last_active_at = now()
        self._emit("input_sent", {"bytes": len(data)})

    def send_action(self, req: ActionRequest) -> Dict[str, Any]:
        actor = req.actor
        action = req.action
        self._authorize_action(actor, req.lock_token)

        if action.type in ("text", "submit", "paste") and action.text is not None:
            reason = is_dangerous_text(action.text)
            if reason and not req.allow_dangerous:
                self._audit(actor, "dangerous_action_blocked", {"reason": reason, "action": action.model_dump()})
                self._emit("dangerous_action_blocked", {"actor": actor, "reason": reason})
                return {"ok": False, "requires_approval": True, "reason": "dangerous_command_pattern", "pattern": reason}

        payload = self._encode_action(action)
        self.write(payload)
        self._audit(actor, "action_sent", {"action": action.model_dump(), "bytes": len(payload.encode('utf-8', errors='ignore'))})
        self._emit("action_sent", {"actor": actor, "action_type": action.type})
        return {"ok": True}

    def _encode_action(self, action: TerminalAction) -> str:
        if action.type == "text":
            if action.text is None:
                raise ValueError("text action requires text")
            return action.text

        if action.type == "submit":
            if action.text is None:
                raise ValueError("submit action requires text")
            return action.text + "\r"

        if action.type == "paste":
            if action.text is None:
                raise ValueError("paste action requires text")
            with self.lock:
                bracketed = self.modes.bracketed_paste
            if bracketed:
                return "\x1b[200~" + action.text + "\x1b[201~"
            return action.text

        if action.type == "control":
            if not action.key:
                raise ValueError("control action requires key")
            return ctrl_key(action.key).decode("latin1")

        if action.type == "resize":
            if not action.rows or not action.cols:
                raise ValueError("resize action requires rows and cols")
            self.resize(action.rows, action.cols)
            return ""

        if action.type == "key":
            if not action.key:
                raise ValueError("key action requires key")
            key = action.key.upper().replace("ARROW", "")
            mods = set(m.upper() for m in action.modifiers)
            if len(key) == 1 and "CTRL" in mods and "A" <= key <= "Z":
                seq = ctrl_key(key).decode("latin1")
            elif key in ("UP", "DOWN", "LEFT", "RIGHT"):
                with self.lock:
                    app_cursor = self.modes.application_cursor_keys
                seq = APPLICATION_CURSOR_KEYS[key] if app_cursor else NORMAL_CURSOR_KEYS[key]
            elif key in BASE_KEYS:
                seq = BASE_KEYS[key]
            else:
                # Allow single printable chars as key actions.
                if len(key) == 1:
                    seq = key
                else:
                    raise ValueError(f"unsupported key: {action.key}")
            if "ALT" in mods:
                seq = apply_alt(seq)
            return seq

        raise ValueError(f"unsupported action type: {action.type}")

    def resize(self, rows: int, cols: int) -> None:
        if self.master_fd is None:
            raise RuntimeError("session is not started")
        with self.cond:
            self.rows = rows
            self.cols = cols
            self.screen.resize(lines=rows, columns=cols)
            set_winsize(self.master_fd, rows, cols)
            self.update_seq += 1
            self.last_update_ts = now()
            self.last_active_at = now()
            self.cond.notify_all()
        if self.process and self.process.pid:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGWINCH)
            except Exception:
                pass
        self._emit("resized", {"rows": rows, "cols": cols})

    # ---------------------------------------------------------------------
    # Process health
    # ---------------------------------------------------------------------
    def _pid_exists(self) -> bool:
        if not self.process or not self.process.pid:
            return False
        try:
            os.kill(self.process.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    # ---------------------------------------------------------------------
    # Observation
    # ---------------------------------------------------------------------
    def observe(self, view: Literal["agent", "debug"] = "agent") -> Dict[str, Any]:
        with self.lock:
            lines = [line.rstrip() for line in self.screen.display]
            visible_text = "\n".join(lines).rstrip()
            stable_for_ms = int((now() - self.last_update_ts) * 1000) if self.last_update_ts else 0
            detected = self._detected_state_locked(visible_text, lines, stable_for_ms)
            obs = {
                "session_id": self.id,
                "timestamp": now(),
                "metadata": {
                    "owner": self.owner,
                    "purpose": self.purpose,
                    "tags": self.tags,
                    "created_at": self.created_at,
                    "last_active_at": self.last_active_at,
                },
                "process": {
                    "state": self.state.value,
                    "pid": self.process.pid if self.process else None,
                    "alive": bool(self.state == SessionState.RUNNING and self.process and self.process.poll() is None),
                    "pid_exists": self._pid_exists() if self.process else False,
                    "reader_alive": self.reader_thread.is_alive() if self.reader_thread else False,
                    "returncode": None if not self.process else self.process.poll(),
                    "command": self.command,
                    "cwd": self.cwd,
                },
                "screen": {
                    "rows": self.rows,
                    "cols": self.cols,
                    "cursor": {
                        "row": self.screen.cursor.y,
                        "col": self.screen.cursor.x,
                        "visible": self.modes.cursor_visible,
                    },
                    "visible_text": visible_text,
                    "lines": lines,
                    "stable_for_ms": stable_for_ms,
                    "update_seq": self.update_seq,
                    "last_changed_at": self.last_update_ts,
                },
                "input_modes": self.modes.to_dict(),
                "detected_state": detected,
                "control": {
                    "lock": self.session_lock.to_dict(),
                    "human_takeover": self.human_takeover,
                    "human_takeover_actor": self.human_takeover_actor,
                    "human_takeover_reason": self.human_takeover_reason,
                },
            }
            if view == "debug":
                obs["debug"] = {
                    "raw_tail": "".join(self.raw_chunks)[-12000:],
                    "events_tail": [e.to_dict() for e in list(self.events)[-50:]],
                    "audit_tail": [a.to_dict() for a in list(self.audit)[-50:]],
                }
            return obs

    def _detected_state_locked(self, text: str, lines: List[str], stable_for_ms: int) -> Dict[str, Any]:
        tail = "\n".join(line.rstrip() for line in text.splitlines()[-8:])
        prompt = any(p.search(tail) for p in PROMPT_PATTERNS)
        confirmation = any(p.search(tail) for p in CONFIRMATION_PATTERNS)
        error = any(p.search(text[-4000:]) for p in ERROR_PATTERNS)
        menu = self._detect_menu(lines)
        shell_prompt = prompt and not self.modes.alternate_screen
        tui = self.modes.alternate_screen or menu
        reasons: List[str] = []
        confidence = 0.0
        if stable_for_ms >= 700:
            reasons.append("screen_stable")
            confidence += 0.35
        if prompt:
            reasons.append("prompt_detected")
            confidence += 0.45
        if confirmation:
            reasons.append("confirmation_detected")
            confidence += 0.20
        if tui and stable_for_ms >= 700:
            reasons.append("tui_stable")
            confidence += 0.15
        status = "likely_ready" if confidence >= 0.55 else "unknown"
        return {
            "input_readiness": {"status": status, "confidence": min(confidence, 0.95), "reasons": reasons},
            "prompt_likely": prompt,
            "shell_prompt_likely": shell_prompt,
            "tui_likely": tui,
            "menu_likely": menu,
            "confirmation_likely": confirmation,
            "error_likely": error,
        }

    def _detect_menu(self, lines: List[str]) -> bool:
        tail = [x.strip() for x in lines[-18:]]
        markers = sum(1 for x in tail if re.match(r"^(>|\*|\d+[.)]|[-•])\s+", x))
        box_chars = sum(1 for x in tail if any(ch in x for ch in "│┌┐└┘─+-|"))
        return markers >= 2 or box_chars >= 3

    # ---------------------------------------------------------------------
    # Wait
    # ---------------------------------------------------------------------
    def wait(self, req: WaitRequest) -> Dict[str, Any]:
        deadline = now() + req.timeout_ms / 1000.0
        with self.cond:
            start_seq = req.start_seq if req.start_seq is not None else self.update_seq
            while True:
                obs = self.observe("agent")
                alive = obs["process"]["alive"]
                if req.until in ("screen_update", "any_activity") and self.update_seq > start_seq:
                    return {"ok": True, "reason": "screen_update", "observation": obs}
                if req.until == "screen_stable" and obs["screen"]["stable_for_ms"] >= req.stable_ms:
                    return {"ok": True, "reason": "screen_stable", "observation": obs}
                if req.until == "input_likely_ready" and obs["detected_state"]["input_readiness"]["status"] == "likely_ready":
                    return {"ok": True, "reason": "input_likely_ready", "observation": obs}
                if req.until == "process_exit" and not alive:
                    return {"ok": True, "reason": "process_exit", "observation": obs}
                remaining = deadline - now()
                if remaining <= 0:
                    return {"ok": False, "reason": "timeout", "observation": obs}
                self.cond.wait(timeout=min(0.2, remaining))


# =============================================================================
# Runtime Registry
# =============================================================================

class TerminalRuntime:
    def __init__(self) -> None:
        self.sessions: Dict[str, TerminalSession] = {}
        self.lock = threading.RLock()
        self.cleaner_started = False

    def create(self, req: CreateSessionRequest) -> TerminalSession:
        with self.lock:
            if len(self.sessions) >= DEFAULT_MAX_SESSIONS:
                raise RuntimeError("max sessions exceeded")
            session_id = req.id or f"session-{uuid.uuid4().hex[:8]}"
            if session_id in self.sessions:
                raise ValueError(f"session already exists: {session_id}")
            req.id = session_id
            s = TerminalSession(req)
            s.start()
            self.sessions[session_id] = s
            return s

    def get(self, session_id: str) -> TerminalSession:
        with self.lock:
            s = self.sessions.get(session_id)
        if not s:
            raise KeyError(session_id)
        return s

    def list(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [
                {
                    "session_id": s.id,
                    "state": s.state.value,
                    "command": s.command,
                    "cwd": s.cwd,
                    "owner": s.owner,
                    "purpose": s.purpose,
                    "tags": s.tags,
                    "pid": s.process.pid if s.process else None,
                    "rows": s.rows,
                    "cols": s.cols,
                    "created_at": s.created_at,
                    "last_active_at": s.last_active_at,
                    "control": {"lock": s.session_lock.to_dict(), "human_takeover": s.human_takeover},
                }
                for s in self.sessions.values()
            ]

    def delete(self, session_id: str, actor: str = "system") -> None:
        with self.lock:
            s = self.sessions.pop(session_id, None)
        if not s:
            raise KeyError(session_id)
        s.stop(actor)

    def start_cleaner(self) -> None:
        if self.cleaner_started:
            return
        self.cleaner_started = True
        threading.Thread(target=self._cleaner_loop, name="atr-cleaner", daemon=True).start()

    def _cleaner_loop(self) -> None:
        while True:
            time.sleep(5)
            victims: List[str] = []
            with self.lock:
                for sid, s in self.sessions.items():
                    if s.idle_ttl_sec and s.idle_ttl_sec > 0:
                        if now() - s.last_active_at > s.idle_ttl_sec:
                            victims.append(sid)
            for sid in victims:
                try:
                    self.delete(sid, actor="cleaner")
                except Exception:
                    pass


runtime = TerminalRuntime()
runtime.start_cleaner()


# =============================================================================
# FastAPI service
# =============================================================================

app = FastAPI(title="Agent Terminal Runtime", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_auth(authorization: Optional[str]) -> None:
    if not API_TOKEN:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def get_session_or_404(session_id: str) -> TerminalSession:
    try:
        return runtime.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


@app.get("/")
def root():
    return RedirectResponse(url="/ui")


@app.get("/ui")
def ui():
    return FileResponse(UI_FILE, media_type="text/html; charset=utf-8")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "version": "1.0.0", "auth_enabled": bool(API_TOKEN), "sessions": len(runtime.sessions)}


@app.post("/sessions")
def create_session(req: CreateSessionRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    try:
        s = runtime.create(req)
        time.sleep(0.1)
        return {"ok": True, "session_id": s.id, "observation": s.observe("agent")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/sessions")
def list_sessions(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    return {"sessions": runtime.list()}


@app.get("/sessions/{session_id}/observe")
def observe(
    session_id: str,
    view: Literal["agent", "debug"] = Query("agent"),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(authorization)
    return get_session_or_404(session_id).observe(view)


@app.get("/sessions/{session_id}/screenshot", response_class=PlainTextResponse)
def screenshot(session_id: str, authorization: Optional[str] = Header(default=None)) -> str:
    require_auth(authorization)
    return get_session_or_404(session_id).observe("agent")["screen"]["visible_text"] + "\n"


@app.post("/sessions/{session_id}/actions")
def act(session_id: str, req: ActionRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    try:
        return get_session_or_404(session_id).send_action(req)
    except PermissionError as e:
        raise HTTPException(status_code=423, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/sessions/{session_id}/wait")
def wait(session_id: str, req: WaitRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    return get_session_or_404(session_id).wait(req)


@app.post("/sessions/{session_id}/locks/acquire")
def acquire_lock(session_id: str, req: LockAcquireRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    try:
        return get_session_or_404(session_id).acquire_lock(req.actor, req.lease_ms, req.force)
    except PermissionError as e:
        raise HTTPException(status_code=423, detail=str(e))


@app.post("/sessions/{session_id}/locks/release")
def release_lock(session_id: str, req: LockReleaseRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    try:
        return get_session_or_404(session_id).release_lock(req.actor, req.lock_token)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/sessions/{session_id}/takeover/start")
def takeover_start(session_id: str, req: TakeoverRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    return get_session_or_404(session_id).start_takeover(req.actor, req.reason)


@app.post("/sessions/{session_id}/takeover/end")
def takeover_end(session_id: str, req: TakeoverRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    return get_session_or_404(session_id).end_takeover(req.actor)


@app.get("/sessions/{session_id}/audit")
def audit(session_id: str, tail: int = Query(200, ge=1, le=2000), authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    s = get_session_or_404(session_id)
    with s.lock:
        entries = [a.to_dict() for a in list(s.audit)[-tail:]]
    return {"session_id": session_id, "audit": entries}


@app.get("/sessions/{session_id}/events/recent")
def recent_events(session_id: str, tail: int = Query(200, ge=1, le=2000), authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    s = get_session_or_404(session_id)
    with s.lock:
        events = [e.to_dict() for e in list(s.events)[-tail:]]
    return {"session_id": session_id, "events": events}


@app.get("/sessions/{session_id}/logs/raw")
def raw_logs(session_id: str, tail: int = Query(12000, ge=1, le=200000), authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(authorization)
    s = get_session_or_404(session_id)
    with s.lock:
        raw = "".join(s.raw_chunks)
    return {"session_id": session_id, "raw_tail": raw[-tail:]}


@app.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    actor: str = Query("anonymous"),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(authorization)
    try:
        runtime.delete(session_id, actor=actor)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


@app.websocket("/sessions/{session_id}/events")
async def events_ws(ws: WebSocket, session_id: str, token: Optional[str] = None) -> None:
    # WebSocket auth uses query token if API_TOKEN is enabled.
    if API_TOKEN and token != API_TOKEN:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        s = runtime.get(session_id)
    except KeyError:
        await ws.send_json({"type": "error", "detail": "session not found"})
        await ws.close()
        return
    q = s.subscribe()
    try:
        await ws.send_json({"type": "connected", "session_id": session_id, "observation": s.observe("agent")})
        while True:
            event = await asyncio.to_thread(q.get)
            payload = event.to_dict()
            if event.type in ("screen_updated", "input_mode_changed", "process_exited", "resized"):
                obs = s.observe("agent")
                payload["observation"] = {
                    "screen": {
                        "visible_text": obs["screen"]["visible_text"],
                        "stable_for_ms": obs["screen"]["stable_for_ms"],
                        "update_seq": obs["screen"]["update_seq"],
                    },
                    "input_modes": obs["input_modes"],
                    "detected_state": obs["detected_state"],
                    "process": obs["process"],
                    "control": obs["control"],
                }
            await ws.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        s.unsubscribe(q)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
