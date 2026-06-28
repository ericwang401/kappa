#!/usr/bin/env python3
"""Print Codex's rolling usage windows for logging.

Codex only surfaces its 5-hour and weekly windows through the interactive
`/status` command, which drives the app-server RPC `account/rateLimits/read`.
This script performs the same RPC non-interactively (no model turn, so it does
not consume the window) and prints a one-line summary, e.g.:

    5h: 2% used, resets 2026-06-28 03:55 UTC | weekly: 1% used, resets 2026-07-03 04:50 UTC (plan=plus)

It is meant to be used as a kappa `status_command` for the codex provider. The
RPC reaches chatgpt.com, so it only works where Codex itself can connect.
"""
from __future__ import annotations

import argparse
import json
import select
import subprocess
import sys
import time
from datetime import datetime, timezone


def read_rate_limits(codex: str, timeout: int) -> dict | None:
    """Drive `codex app-server` over stdio and return its rateLimits result.

    stdin is held open while we read replies: the app-server answers
    account/rateLimits/read asynchronously (it reaches the network), so closing
    stdin first would let it exit before the reply lands.
    """
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"clientInfo": {"name": "kappa", "title": "kappa", "version": "0.1.0"}},
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 1, "method": "account/rateLimits/read", "params": {}},
    ]
    stdin = "".join(json.dumps(message) + "\n" for message in messages)
    proc = subprocess.Popen(
        [codex, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        proc.stdin.write(stdin)
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], deadline - time.monotonic())
            if not ready:
                break
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1 and isinstance(message.get("result"), dict):
                return message["result"].get("rateLimits")
        return None
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def format_window(window: dict | None) -> str:
    if not isinstance(window, dict):
        return "n/a"
    used = window.get("usedPercent")
    resets_at = window.get("resetsAt")
    when = "?"
    if isinstance(resets_at, (int, float)):
        when = datetime.fromtimestamp(resets_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{used}% used, resets {when}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="codex executable (default: codex)")
    parser.add_argument("--timeout", type=int, default=30, help="seconds to wait (default: 30)")
    args = parser.parse_args()

    try:
        rate_limits = read_rate_limits(args.codex, args.timeout)
    except FileNotFoundError:
        print(f"codex usage unavailable: {args.codex} not found", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"codex usage unavailable: timed out after {args.timeout}s", file=sys.stderr)
        return 1

    if not rate_limits:
        print("codex usage unavailable: no rateLimits in app-server response", file=sys.stderr)
        return 1

    print(
        f"5h: {format_window(rate_limits.get('primary'))}"
        f" | weekly: {format_window(rate_limits.get('secondary'))}"
        f" (plan={rate_limits.get('planType')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
