# kappa

`kappa` is a small Linux cron harness for running lightweight Claude, opencode, and Codex CLI prompts at fixed times.

It does not run a daemon, web server, SDK, MCP server, or extra project tooling. Cron starts the script, the script runs the configured CLI command, logs the result, then exits.

## Requirements

- Linux
- Python 3.11+
- `cron`
- Any provider CLIs you enable in `kappa.toml`

Check Python:

```bash
python3 --version
```

## Configure

Edit `kappa.toml`.

The default prompt is intentionally minimal:

```toml
prompt = "Reply exactly: OK"
```

If cron cannot find a provider command, update `path` or use absolute command paths in each provider's `command` array. The default includes common Linux paths plus `/opt/homebrew/bin` for local macOS testing.

The default Claude command uses the `haiku` alias with low effort and a small per-run budget cap. The default Codex command does not pin a model name, because Codex's lower-cost model IDs change over time; it only requests low reasoning effort and low verbosity.

## Check Setup

```bash
python3 kappa.py doctor
```

This checks the config, timezone, log directory, lock directory, and enabled provider commands.

## Run Manually

Run all enabled providers:

```bash
python3 kappa.py run
```

Run one provider:

```bash
python3 kappa.py run claude
python3 kappa.py run opencode
python3 kappa.py run codex
```

Provider names passed manually run even if `enabled = false`; `enabled` controls the scheduled all-provider run.

## Cron

Print the recommended cron entry:

```bash
python3 kappa.py cron
```

Default schedule:

```cron
CRON_TZ=America/New_York
30 7 * * * /usr/bin/python3 /absolute/path/to/kappa.py --config /absolute/path/to/kappa.toml run
35 12 * * * /usr/bin/python3 /absolute/path/to/kappa.py --config /absolute/path/to/kappa.toml run
40 17 * * * /usr/bin/python3 /absolute/path/to/kappa.py --config /absolute/path/to/kappa.toml run
45 22 * * * /usr/bin/python3 /absolute/path/to/kappa.py --config /absolute/path/to/kappa.toml run
30 2 * * * /usr/bin/python3 /absolute/path/to/kappa.py --config /absolute/path/to/kappa.toml run
```

That runs at:

```text
7:30 AM ET
12:35 PM ET
5:40 PM ET
10:45 PM ET
2:30 AM ET
```

The usage window does not reset on a fixed clock; it resets roughly five hours
after the window's first message lands. The daytime fire times are spaced a
little over five hours apart (5h5m) so each one lands just after the previous
window resets instead of just before it, rather than at an exact five‑hour mark.

Install it with:

```bash
crontab -e
```

Paste the lines printed by `python3 kappa.py cron` (the `CRON_TZ` line plus one line per fire time).

## Logs

Default log file:

```text
~/.local/state/kappa/kappa.log
```

Each run records provider start, success, failure, timeout, or skipped overlapping execution.

### Log Rotation

The sample `logrotate.kappa` config keeps logs bounded for a root-owned VPS install. It rotates weekly and keeps two compressed archives.

Install it with:

```bash
sudo cp /opt/kappa/logrotate.kappa /etc/logrotate.d/kappa
sudo chmod 644 /etc/logrotate.d/kappa
```

Check that logrotate accepts the config:

```bash
sudo logrotate -d /etc/logrotate.d/kappa
```

Apply it immediately once, if desired:

```bash
sudo logrotate /etc/logrotate.d/kappa
```

## Locking

`kappa` uses a non-blocking lock file so overlapping cron runs do not stack up.

Default lock file:

```text
~/.local/state/kappa/kappa.lock
```
