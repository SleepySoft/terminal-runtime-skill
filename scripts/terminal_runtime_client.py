#!/usr/bin/env python3
"""CLI helper for Agent Terminal Runtime.

This helper intentionally uses only Python standard library so a skill can call it
without installing extra packages.

Environment variables:
  ATR_BASE_URL   default: http://127.0.0.1:18650
  ATR_API_TOKEN  optional bearer token
  ATR_SESSION_ID optional default session id
  ATR_ACTOR      optional default actor, default openclaw
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

BASE_URL = os.environ.get("ATR_BASE_URL", "http://127.0.0.1:18650").rstrip("/")
TOKEN = os.environ.get("ATR_API_TOKEN", "")
DEFAULT_SESSION_ID = os.environ.get("ATR_SESSION_ID", "bash-main")
DEFAULT_ACTOR = os.environ.get("ATR_ACTOR", "openclaw")


def request(method: str, path: str, body: Optional[Dict[str, Any]] = None, plain: bool = False) -> Any:
    url = BASE_URL + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if plain:
                return raw
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"text": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(raw, file=sys.stderr)
        raise SystemExit(e.code)


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_health(args: argparse.Namespace) -> None:
    print_json(request("GET", "/health"))


def cmd_observe(args: argparse.Namespace) -> None:
    qs = urllib.parse.urlencode({"view": args.view})
    print_json(request("GET", f"/sessions/{urllib.parse.quote(args.session_id)}/observe?{qs}"))


def cmd_screenshot(args: argparse.Namespace) -> None:
    text = request("GET", f"/sessions/{urllib.parse.quote(args.session_id)}/screenshot", plain=True)
    print(text, end="")


def cmd_history(args: argparse.Namespace) -> None:
    qs = urllib.parse.urlencode({"tail": args.tail, "offset": args.offset, "limit": args.limit})
    data = request("GET", f"/sessions/{urllib.parse.quote(args.session_id)}/history/scrollback?{qs}")
    if args.plain:
        for line in data.get("scrollback", []):
            print(line)
    else:
        print_json(data)


def cmd_history_reset(args: argparse.Namespace) -> None:
    print_json(request("POST", f"/sessions/{urllib.parse.quote(args.session_id)}/history/reset?actor={urllib.parse.quote(args.actor)}"))


def cmd_act(args: argparse.Namespace) -> None:
    action: Dict[str, Any] = {"type": args.type}
    if args.text is not None:
        action["text"] = args.text
    if args.key is not None:
        action["key"] = args.key
    if args.rows is not None:
        action["rows"] = args.rows
    if args.cols is not None:
        action["cols"] = args.cols
    if args.modifier:
        action["modifiers"] = args.modifier
    body = {
        "actor": args.actor,
        "lock_token": args.lock_token,
        "allow_dangerous": args.allow_dangerous,
        "action": action,
    }
    print_json(request("POST", f"/sessions/{urllib.parse.quote(args.session_id)}/actions", body))


def cmd_wait(args: argparse.Namespace) -> None:
    body = {"until": args.until, "timeout_ms": args.timeout_ms, "stable_ms": args.stable_ms}
    print_json(request("POST", f"/sessions/{urllib.parse.quote(args.session_id)}/wait", body))


def cmd_lock_acquire(args: argparse.Namespace) -> None:
    body = {"actor": args.actor, "lease_ms": args.lease_ms, "force": args.force}
    print_json(request("POST", f"/sessions/{urllib.parse.quote(args.session_id)}/locks/acquire", body))


def cmd_lock_release(args: argparse.Namespace) -> None:
    body = {"actor": args.actor, "lock_token": args.lock_token}
    print_json(request("POST", f"/sessions/{urllib.parse.quote(args.session_id)}/locks/release", body))


def cmd_list(args: argparse.Namespace) -> None:
    print_json(request("GET", "/sessions"))


def main() -> None:
    p = argparse.ArgumentParser(description="Agent Terminal Runtime helper")
    sub = p.add_subparsers(required=True)

    sp = sub.add_parser("health")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("list")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("observe")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--view", default="agent", choices=["agent", "debug"])
    sp.set_defaults(func=cmd_observe)

    sp = sub.add_parser("screenshot")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.set_defaults(func=cmd_screenshot)

    sp = sub.add_parser("history")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--tail", type=int, default=200)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--plain", action="store_true", help="print as plain text instead of JSON")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("history-reset")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--actor", default=DEFAULT_ACTOR)
    sp.set_defaults(func=cmd_history_reset)

    sp = sub.add_parser("act")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--actor", default=DEFAULT_ACTOR)
    sp.add_argument("--lock-token", default=None)
    sp.add_argument("--allow-dangerous", action="store_true")
    sp.add_argument("--type", required=True, choices=["text", "submit", "paste", "key", "control", "resize"])
    sp.add_argument("--text")
    sp.add_argument("--key")
    sp.add_argument("--modifier", action="append", choices=["CTRL", "ALT", "SHIFT"])
    sp.add_argument("--rows", type=int)
    sp.add_argument("--cols", type=int)
    sp.set_defaults(func=cmd_act)

    sp = sub.add_parser("wait")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--until", default="screen_stable", choices=["screen_update", "screen_stable", "input_likely_ready", "process_exit", "any_activity"])
    sp.add_argument("--timeout-ms", type=int, default=30000)
    sp.add_argument("--stable-ms", type=int, default=1000)
    sp.set_defaults(func=cmd_wait)

    sp = sub.add_parser("lock-acquire")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--actor", default=DEFAULT_ACTOR)
    sp.add_argument("--lease-ms", type=int, default=30000)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_lock_acquire)

    sp = sub.add_parser("lock-release")
    sp.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    sp.add_argument("--actor", default=DEFAULT_ACTOR)
    sp.add_argument("--lock-token", required=True)
    sp.set_defaults(func=cmd_lock_release)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
