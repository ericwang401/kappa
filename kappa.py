#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
        if "timeout_seconds" in provider:
            provider_timeout = provider["timeout_seconds"]
            if not isinstance(provider_timeout, int) or provider_timeout <= 0:
                raise ConfigError(
                    f"providers.{name}.timeout_seconds must be a positive integer"
                )
        if "check_url" in provider and (
            not isinstance(provider["check_url"], str) or not provider["check_url"]
        ):
            raise ConfigError(f"providers.{name}.check_url must be a non-empty string")
        provider_env = provider.get("env")
        if provider_env is not None and (
            not isinstance(provider_env, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in provider_env.items()
            )
        ):
            raise ConfigError(f"providers.{name}.env must be a table of string values")
        status_command = provider.get("status_command")
        if status_command is not None and (
            not isinstance(status_command, list)
            or not status_command
            or not all(isinstance(part, str) for part in status_command)
        ):
            raise ConfigError(
                f"providers.{name}.status_command must be a non-empty string array"
            )

    return config


# Substrings that explain *why* a provider run failed, so the log says more than
# "timeout" or "error". Network signatures usually mean the host cannot reach the
# provider API at all (e.g. a datacenter IP blocked by the provider's edge).
NETWORK_SIGNATURES = (
    "connection reset by peer",
    "recv failure",
    "error sending request",
    "failed to connect to websocket",
    "transport channel closed",
    "reconnecting...",
    "connection refused",
    "temporary failure in name resolution",
    "timed out",
)
LIMIT_SIGNATURES = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota",
    "try again at",
)


def failure_hint(*chunks: str) -> str:
    """Classify provider output into a short hint for the log line."""
    text = " ".join(chunk for chunk in chunks if chunk).lower()
    if any(sig in text for sig in LIMIT_SIGNATURES):
        return " hint=usage-limit"
    if any(sig in text for sig in NETWORK_SIGNATURES):
        return " hint=network-unreachable"
    return ""


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


def build_env(config: dict[str, Any], provider: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = expand_path_list(config["path"])
    # Per-provider env overrides let one provider route through a proxy / exit
    # node (e.g. HTTPS_PROXY, ALL_PROXY) without forcing it on the others.
    env.update(provider.get("env", {}))
    return env


def log_window(config: dict[str, Any], name: str) -> None:
    """Best-effort: run a provider's status_command and log the window state.

    Some CLIs report the rolling usage window (e.g. `claude /usage` prints
    "Current session: 51% used · resets ..."). This is a read-only status query,
    not a model call, so it does not consume the window it reports on. Failures
    here never affect the warmup result.
    """
    provider = config["providers"][name]
    status_command = provider.get("status_command")
    if not status_command:
        return
    timeout = provider.get("timeout_seconds", config["timeout_seconds"])
    try:
        result = subprocess.run(
            status_command,
            env=build_env(config, provider),
            timeout=timeout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    text = " ".join((result.stdout or result.stderr or "").split())[:300]
    if text:
        log_line(config, f"provider={name} status=window detail={quote(text)}")


def run_provider(config: dict[str, Any], name: str) -> bool:
    provider = config["providers"][name]
    command = [*provider["command"], config["prompt"]]
    timeout = provider.get("timeout_seconds", config["timeout_seconds"])
    env = build_env(config, provider)

    command_text = " ".join(quote(part) for part in command)
    log_line(config, f"provider={name} status=start timeout={timeout}s command={command_text}")
    start = time.monotonic()

    try:
        result = subprocess.run(
            command,
            env=env,
            timeout=timeout,
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
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        partial = (exc.stdout or "") + " " + (exc.stderr or "")
        hint = failure_hint(partial)
        log_line(config, f"provider={name} status=timeout duration={duration:.2f}s{hint}")
        print(f"{name}: timed out after {timeout}s", file=sys.stderr)
        return False

    duration = time.monotonic() - start
    if result.returncode == 0:
        # Record the model's reply so the log proves the prompt actually landed
        # (and so a window-warming run is distinguishable from a silent no-op).
        reply = " ".join(result.stdout.split())[:200]
        reply_field = f" reply={quote(reply)}" if reply else " reply="
        log_line(config, f"provider={name} status=ok exit=0 duration={duration:.2f}s{reply_field}")
        log_window(config, name)
        print(f"{name}: ok")
        return True

    stderr = " ".join(result.stderr.split())[:500]
    detail = f" stderr={quote(stderr)}" if stderr else ""
    hint = failure_hint(result.stderr, result.stdout)
    log_line(
        config,
        f"provider={name} status=error exit={result.returncode} duration={duration:.2f}s{hint}{detail}",
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

        proxies = proxies_from_env(provider.get("env", {}))
        if proxies:
            print(f"  proxy: {', '.join(sorted(proxies.values()))}")
        check_url = provider.get("check_url")
        if check_url:
            reachable, detail = check_reachable(check_url, proxies=proxies)
            if reachable:
                print(f"  reach {check_url}: ok ({detail})")
            else:
                print(f"  reach {check_url}: FAIL ({detail})", file=sys.stderr)
                if provider["enabled"]:
                    ok = False

    return 0 if ok else 1


def proxies_from_env(env: dict[str, str]) -> dict[str, str]:
    """Extract http/https proxy settings from a provider's env overrides.

    Lets `doctor` probe the same routed path a provider would use. SOCKS proxies
    (ALL_PROXY=socks5://...) need PySocks for the probe and are skipped here, but
    they still work for the provider run itself.
    """
    proxies: dict[str, str] = {}
    for scheme in ("http", "https"):
        for key in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY"):
            value = env.get(key)
            if value and value.lower().startswith(("http://", "https://")):
                proxies[scheme] = value
    return proxies


def check_reachable(
    url: str, timeout: float = 10.0, proxies: dict[str, str] | None = None
) -> tuple[bool, str]:
    """Best-effort reachability probe for a provider API host.

    A blocked datacenter IP shows up here as a connection reset / refused, which
    is the failure mode that silently breaks a provider while the CLI is healthy.
    Any HTTP response (even 401/403/404) counts as reachable. When `proxies` is
    given, the probe goes through it so it tests the same routed path.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies or {}))
    request = urllib.request.Request(url, method="HEAD")
    start = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            return True, f"http {response.status} in {time.monotonic() - start:.2f}s"
    except urllib.error.HTTPError as error:
        return True, f"http {error.code} in {time.monotonic() - start:.2f}s"
    except urllib.error.URLError as error:
        return False, f"{error.reason} after {time.monotonic() - start:.2f}s"
    except OSError as error:
        return False, f"{error} after {time.monotonic() - start:.2f}s"


def cron(args: argparse.Namespace) -> int:
    config_path = expand_path(args.config).resolve()
    script_path = Path(__file__).resolve()
    config = load_config(config_path)
    python = quote(sys.executable)
    script = quote(str(script_path))
    config_arg = quote(str(config_path))
    timezone = config["timezone"]
    invocation = f"{python} {script} --config {config_arg} run"

    if args.cron_d:
        # Debian/Ubuntu cron honors CRON_TZ in /etc/crontab and /etc/cron.d files,
        # where each line also names the user to run as.
        print(f"# Install to /etc/cron.d/kappa (root-owned, not group/other-writable).")
        print(f"CRON_TZ={timezone}")
        for schedule in args.schedule:
            print(f"{schedule} {quote(args.user)} {invocation}")
    else:
        # CRON_TZ is ignored in a per-user crontab, so a bare line would be a
        # silent no-op. Times run in the host timezone instead.
        print(f"# These entries run in the host timezone, NOT {timezone}: CRON_TZ is")
        print(f"# ignored in a user crontab. Match the host clock with:")
        print(f"#   sudo timedatectl set-timezone {timezone}")
        print(f"# or install under /etc/cron.d instead with: kappa cron --cron-d")
        for schedule in args.schedule:
            print(f"{schedule} {invocation}")
    return 0


def cron_to_oncalendar(expr: str, timezone: str) -> str:
    """Convert a daily 'M H * * *' cron expression to a systemd OnCalendar line.

    systemd timers (unlike Debian cron) honor a timezone suffix on OnCalendar and
    handle DST, so this is the reliable way to anchor fire times to a zone while
    the host clock stays on UTC.
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ConfigError(f"cannot convert schedule to OnCalendar: {expr!r}")
    minute, hour, dom, month, dow = parts
    if (dom, month, dow) != ("*", "*", "*"):
        raise ConfigError(
            f"systemd output supports only daily 'M H * * *' schedules; got {expr!r}"
        )
    try:
        hour_n, minute_n = int(hour), int(minute)
    except ValueError:
        raise ConfigError(f"cannot convert schedule to OnCalendar: {expr!r}")
    return f"*-*-* {hour_n:02d}:{minute_n:02d}:00 {timezone}"


def systemd(args: argparse.Namespace) -> int:
    config_path = expand_path(args.config).resolve()
    script_path = Path(__file__).resolve()
    config = load_config(config_path)
    timezone = config["timezone"]
    oncalendars = [cron_to_oncalendar(expr, timezone) for expr in args.schedule]
    exec_start = f"{sys.executable} {script_path} --config {config_path} run"

    # User= makes systemd resolve the account from passwd and set $HOME/$USER/
    # $LOGNAME accordingly, so the config's ~-relative path and the CLIs' own
    # credential dirs (~/.claude, ~/.codex) resolve to that user, not root.
    run_as = f"User={args.user}\n" if args.user else ""
    service = (
        "[Unit]\n"
        "Description=kappa warmup run (keeps AI usage windows warm)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"{run_as}"
        f"ExecStart={exec_start}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=kappa warmup schedule (times in {timezone}, DST-correct)\n\n"
        "[Timer]\n"
        + "".join(f"OnCalendar={entry}\n" for entry in oncalendars)
        + "AccuracySec=30s\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    service_path = Path(args.unit_dir) / f"{args.name}.service"
    timer_path = Path(args.unit_dir) / f"{args.name}.timer"

    if args.write:
        service_path.write_text(service, encoding="utf-8")
        timer_path.write_text(timer, encoding="utf-8")
        print(f"wrote {service_path}")
        print(f"wrote {timer_path}")
        print("next:")
        print("  sudo systemctl daemon-reload")
        print(f"  sudo systemctl enable --now {args.name}.timer")
        print(f"  systemctl list-timers {args.name}.timer")
        return 0

    print(f"# ===== {service_path} =====")
    print(service)
    print(f"# ===== {timer_path} =====")
    print(timer)
    print("# Write these files (or re-run with --write), then:")
    print("#   sudo systemctl daemon-reload")
    print(f"#   sudo systemctl enable --now {args.name}.timer")
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
    cron_parser.add_argument(
        "--cron-d",
        action="store_true",
        help="emit /etc/cron.d format (CRON_TZ honored, lines include the run-as user)",
    )
    cron_parser.add_argument(
        "--user",
        default="root",
        help="run-as user for --cron-d lines (default: root)",
    )
    cron_parser.set_defaults(func=cron)

    systemd_parser = subcommands.add_parser(
        "systemd",
        parents=[common],
        help="print (or write) a systemd service + timer (TZ-aware, DST-correct)",
    )
    systemd_parser.add_argument(
        "--schedule",
        nargs="+",
        default=DEFAULT_SCHEDULE,
        help="daily 'M H * * *' expressions (default: the routine fire times)",
    )
    systemd_parser.add_argument("--name", default="kappa", help="unit base name (default: kappa)")
    systemd_parser.add_argument(
        "--user",
        default="",
        help="run the service as this user via User= (default: unset, i.e. root)",
    )
    systemd_parser.add_argument(
        "--unit-dir",
        default="/etc/systemd/system",
        help="directory for the unit files (default: /etc/systemd/system)",
    )
    systemd_parser.add_argument(
        "--write",
        action="store_true",
        help="write the unit files to --unit-dir instead of printing them",
    )
    systemd_parser.set_defaults(func=systemd)

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
