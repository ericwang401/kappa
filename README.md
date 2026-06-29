# kappa

`kappa` keeps lightweight Claude, opencode, and Codex CLI usage windows warm by
running a tiny prompt at fixed times. No daemon, web server, SDK, or MCP server —
a scheduler starts the script, it runs the configured CLI command, logs the
result, then exits.

## Requirements

- Linux, Python 3.11+ (`python3 --version`)
- A scheduler: systemd (recommended) or cron
- Any provider CLIs you enable in `kappa.toml`

## Quickstart

```bash
python3 kappa.py doctor      # check config, timezone, and provider CLIs
python3 kappa.py run         # run all enabled providers once
python3 kappa.py run claude  # run one provider (even if enabled = false)
```

Provider names passed manually run even if `enabled = false`; `enabled` controls
the scheduled all-provider run.

Then put it on a schedule — see [docs/scheduling.md](docs/scheduling.md). The
short version, running as the user who logged into the CLIs:

```bash
sudo python3 kappa.py systemd --user eric --write
sudo systemctl daemon-reload
sudo systemctl enable --now kappa.timer
```

## How it works

The scheduler starts `kappa.py run`. It takes a non-blocking lock (so overlapping
runs don't stack), runs each enabled provider's `command` with the configured
`prompt`, logs `start`/`ok`/`error`/`timeout`, then exits.

## Docs

- [Configuration](docs/configuration.md) — `kappa.toml`, providers, per-provider
  keys, timezone, `doctor`.
- [Scheduling](docs/scheduling.md) — systemd timer / cron, the timezone trap,
  running as the right user, **install & uninstall**.
- [Networking](docs/networking.md) — routing a blocked provider (e.g. Codex on a
  VPS) through a proxy or exit node.
- [Operations](docs/operations.md) — logs, log rotation, locking.
