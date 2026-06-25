#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from shlex import quote
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - this project targets Linux.
    fcntl = None

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 only.
    tomllib = None

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9 only.
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


MIN_PYTHON = (3, 11)
DEFAULT_CONFIG = Path(__file__).with_name("kappa.toml")
# Fixed fire times anchored to the daily routine. The daytime slots are spaced a
# little over five hours apart so each lands just after the previous usage window
# resets (the window resets ~5h after its first message, not on a fixed clock).
DEFAULT_SCHEDULE = [
    "30 7 * * *",   # 7:30 AM
    "35 12 * * *",  # 12:35 PM
    "40 17 * * *",  # 5:40 PM
    "45 22 * * *",  # 10:45 PM
    "30 2 * * *",   # 2:30 AM
]


class ConfigError(Exception):
    pass


def expand_path(value: str) -> Path:
    return Path(value).expanduser()


def expand_path_list(value: str) -> str:
    parts = []
    for part in value.split(os.pathsep):
        if part.startswith("~"):
            parts.append(str(Path(part).expanduser()))
        else:
            parts.append(part)
    return os.pathsep.join(parts)


def require_runtime() -> None:
    if sys.version_info < MIN_PYTHON:
        raise ConfigError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"found {sys.version_info.major}.{sys.version_info.minor}"
        )
    if tomllib is None:
        raise ConfigError("tomllib is unavailable; use Python 3.11+")
    if fcntl is None:
        raise ConfigError("fcntl is unavailable; kappa targets Linux")


def load_config(path: Path) -> dict[str, Any]:
    require_runtime()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    with path.open("rb") as handle:
        config = tomllib.load(handle)

    if not isinstance(config, dict):
        raise ConfigError("config must be a TOML table")

    config.setdefault("timeout_seconds", 90)
    config.setdefault("prompt", "Reply exactly: OK")
    config.setdefault("timezone", "America/New_York")
    config.setdefault("log_file", "~/.local/state/kappa/kappa.log")
    config.setdefault("lock_file", "~/.local/state/kappa/kappa.lock")
    config.setdefault("path", "~/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")

    timeout = config["timeout_seconds"]
    if not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError("timeout_seconds must be a positive integer")

    for key in ("prompt", "timezone", "log_file", "lock_file", "path"):
        if not isinstance(config[key], str) or not config[key]:
            raise ConfigError(f"{key} must be a non-empty string")

    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ConfigError("providers must contain at least one provider")

    for name, provider in providers.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("provider names must be non-empty strings")
        if not isinstance(provider, dict):
            raise ConfigError(f"providers.{name} must be a table")
        provider.setdefault("enabled", False)
        if not isinstance(provider["enabled"], bool):
            raise ConfigError(f"providers.{name}.enabled must be true or false")
        command = provider.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) for part in command)
        ):
            raise ConfigError(f"providers.{name}.command must be a non-empty string array")

    return config


def now(config: dict[str, Any]) -> str:
    timezone = config["timezone"]
    try:
        tz = ZoneInfo(timezone) if ZoneInfo is not None else None
    except ZoneInfoNotFoundError:
        tz = None
    return datetime.now(tz).isoformat(timespec="seconds")


def log_line(config: dict[str, Any], message: str) -> None:
    log_file = expand_path(config["log_file"])
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{now(config)} {message}\n")


def acquire_lock(config: dict[str, Any]):
    lock_file = expand_path(config["lock_file"])
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def command_exists(command: str, env_path: str) -> bool:
    if os.sep in command:
        return os.access(Path(command).expanduser(), os.X_OK)
    return shutil.which(command, path=env_path) is not None


def provider_names(config: dict[str, Any], requested: list[str]) -> list[str]:
    providers = config["providers"]
    if requested:
        missing = [name for name in requested if name not in providers]
        if missing:
            known = ", ".join(sorted(providers))
            raise ConfigError(f"unknown provider(s): {', '.join(missing)}; known: {known}")
        return requested
    return [name for name, provider in providers.items() if provider["enabled"]]


def run_provider(config: dict[str, Any], name: str) -> bool:
    provider = config["providers"][name]
    command = [*provider["command"], config["prompt"]]
    env = os.environ.copy()
    env["PATH"] = expand_path_list(config["path"])

    command_text = " ".join(quote(part) for part in command)
    log_line(config, f"provider={name} status=start command={command_text}")
    start = time.monotonic()

    try:
        result = subprocess.run(
            command,
            env=env,
            timeout=config["timeout_seconds"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        duration = time.monotonic() - start
        log_line(config, f"provider={name} status=missing duration={duration:.2f}s")
        print(f"{name}: command not found: {command[0]}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        log_line(config, f"provider={name} status=timeout duration={duration:.2f}s")
        print(f"{name}: timed out after {config['timeout_seconds']}s", file=sys.stderr)
        return False

    duration = time.monotonic() - start
    if result.returncode == 0:
        log_line(config, f"provider={name} status=ok exit=0 duration={duration:.2f}s")
        print(f"{name}: ok")
        return True

    stderr = " ".join(result.stderr.split())[:500]
    detail = f" stderr={quote(stderr)}" if stderr else ""
    log_line(
        config,
        f"provider={name} status=error exit={result.returncode} duration={duration:.2f}s{detail}",
    )
    print(f"{name}: failed with exit {result.returncode}", file=sys.stderr)
    return False


def run(args: argparse.Namespace) -> int:
    config = load_config(expand_path(args.config))
    names = provider_names(config, args.providers)
    if not names:
        print("no enabled providers", file=sys.stderr)
        return 1

    lock = acquire_lock(config)
    if lock is None:
        log_line(config, "status=locked action=skip")
        print("another kappa run is already active; skipping")
        return 0

    with lock:
        results = [run_provider(config, name) for name in names]
    return 0 if all(results) else 1


def doctor(args: argparse.Namespace) -> int:
    ok = True
    print(f"python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    try:
        config = load_config(expand_path(args.config))
    except ConfigError as error:
        print(f"config: error: {error}", file=sys.stderr)
        return 1

    print(f"config: {expand_path(args.config)}")

    try:
        ZoneInfo(config["timezone"])
        print(f"timezone: {config['timezone']}")
    except ZoneInfoNotFoundError:
        print(f"timezone: missing zoneinfo data for {config['timezone']}", file=sys.stderr)
        ok = False

    env_path = expand_path_list(config["path"])
    print(f"path: {env_path}")

    for label, path_key in (("log", "log_file"), ("lock", "lock_file")):
        path = expand_path(config[path_key])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            print(f"{label}: {path}")
        except OSError as error:
            print(f"{label}: error: {error}", file=sys.stderr)
            ok = False

    for name, provider in config["providers"].items():
        command = provider["command"][0]
        status = "enabled" if provider["enabled"] else "disabled"
        if command_exists(command, env_path):
            print(f"provider {name}: {status}, found {command}")
        else:
            print(f"provider {name}: {status}, missing {command}", file=sys.stderr)
            if provider["enabled"]:
                ok = False

    return 0 if ok else 1


def cron(args: argparse.Namespace) -> int:
    config_path = expand_path(args.config).resolve()
    script_path = Path(__file__).resolve()
    config = load_config(config_path)
    python = quote(sys.executable)
    script = quote(str(script_path))
    config_arg = quote(str(config_path))
    print(f"CRON_TZ={config['timezone']}")
    for schedule in args.schedule:
        print(f"{schedule} {python} {script} --config {config_arg} run")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"path to config file (default: {DEFAULT_CONFIG})",
    )

    parser = argparse.ArgumentParser(
        description="Keep lightweight AI CLI windows warm on a schedule.",
        parents=[common],
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", parents=[common], help="run enabled providers")
    run_parser.add_argument("providers", nargs="*", help="optional provider names to run")
    run_parser.set_defaults(func=run)

    doctor_parser = subcommands.add_parser("doctor", parents=[common], help="check config and commands")
    doctor_parser.set_defaults(func=doctor)

    cron_parser = subcommands.add_parser("cron", parents=[common], help="print Linux cron entries")
    cron_parser.add_argument(
        "--schedule",
        nargs="+",
        default=DEFAULT_SCHEDULE,
        help="one or more cron schedule expressions (default: the routine fire times)",
    )
    cron_parser.set_defaults(func=cron)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
