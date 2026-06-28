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

### Optional per-provider keys

Each `[providers.*]` table accepts a few optional keys beyond `enabled` and `command`:

- `timeout_seconds` — overrides the global `timeout_seconds` for just this provider. Codex reasoning can run well past the global 90s cap, and a run killed mid-flight never lands a message (so its usage window never starts), so the default config gives Codex `180`.
- `check_url` — a URL that `kappa doctor` probes to confirm the provider's API host is reachable from this machine. Any HTTP response (even 401/403/404) counts as reachable; a connection reset or refusal is reported as a failure.
- `env` — a table of environment variables applied to only this provider's run. Use it to route one provider through a proxy or exit node (see [Routing a blocked provider](#routing-a-blocked-provider)) without affecting the others.

## Check Setup

```bash
python3 kappa.py doctor
```

This checks the config, timezone, log directory, lock directory, and enabled provider commands. For any provider with a `check_url`, it also probes whether that API host is reachable from this machine — the quickest way to spot a provider whose CLI is installed and logged in but whose endpoint is blocked from this network (see [Routing a blocked provider](#routing-a-blocked-provider)).

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

### Timezone

The fire times only make sense if cron actually interprets them in the intended
zone. **`CRON_TZ` is not honored in a per-user crontab** on Debian/Ubuntu's cron
(the `crontab -e` kind); the jobs run in the host's system timezone instead. On a
UTC host that silently shifts every fire time by 4–5 hours, so a window you
expected at 7:30 AM ET actually anchors at 3:30 AM ET.

Pick one:

- **Set the host timezone** to the zone you want (simplest, handles DST):

  ```bash
  sudo timedatectl set-timezone America/New_York
  sudo systemctl restart cron
  ```

- **Or install via `/etc/cron.d`** instead of `crontab -e`, where Debian cron
  does honor `CRON_TZ`. Keep the system clock on UTC and drop the lines (with the
  `CRON_TZ` line) into `/etc/cron.d/kappa`, adding the runas user to each line:

  ```cron
  CRON_TZ=America/New_York
  30 7 * * * root /usr/bin/python3 /opt/kappa/kappa.py --config /opt/kappa/kappa.toml run
  ```

Confirm what cron actually did by comparing the timestamps in `kappa.log` (or
`journalctl -u cron`) against the schedule.

Install it with:

```bash
crontab -e
```

Paste the lines printed by `python3 kappa.py cron` (the `CRON_TZ` line plus one line per fire time).

## Routing a blocked provider

Some provider endpoints are unreachable from datacenter/VPS IPs. Codex's
ChatGPT-login endpoint (`chatgpt.com`) in particular is fronted by Cloudflare,
which resets connections from hosting-provider IP ranges and flags the Linux
CLI's TLS fingerprint as a bot — so the CLI is installed and logged in, yet every
run resets or times out while `api.anthropic.com` (Claude) stays reachable. This
is an IP/TLS reputation block, not a rate limit, so lowering the cron frequency
does not help. `kappa doctor` surfaces it via `check_url`:

```text
reach https://api.anthropic.com/: ok (http 404 in 0.05s)
reach https://chatgpt.com/:      FAIL (Connection reset by peer ...)
```

To fix it, route just the affected provider through a non-datacenter egress using
its per-provider `env` table — kappa stays provider-agnostic, so this is opt-in
and not wired to any specific tool:

```toml
[providers.codex]
# ...existing keys...

# Through an HTTP proxy on a residential box (doctor probes this path too):
[providers.codex.env]
HTTPS_PROXY = "http://10.0.0.5:8080"
```

Other options that need no kappa change:

- **Tailscale exit node / residential egress** — expose a SOCKS or HTTP proxy on
  a residential machine (e.g. a Tailscale node) and point the provider's
  `env` at it (`ALL_PROXY = "socks5://127.0.0.1:1055"`). SOCKS proxies are used
  at run time but not probed by `doctor` (the probe needs an HTTP/HTTPS proxy).
- **Wrap the command**, e.g. `command = ["proxychains4", "codex", "exec", ...]`.
- **Run that provider's warmup from a residential machine** (laptop, home server)
  on its own crontab, and leave only the reachable providers on the VPS.

Using an API key instead of the ChatGPT login avoids `chatgpt.com` entirely, but
it bills per token and does not warm the subscription's usage window, so it
defeats the purpose of a warmup ping.

## Logs

Default log file:

```text
~/.local/state/kappa/kappa.log
```

Each run records provider start, success, failure, timeout, or skipped overlapping execution.

A successful run logs the model's (truncated) reply, e.g. `status=ok ... reply=OK`, so you can confirm the prompt actually landed and warmed the window rather than failing silently. Failures and timeouts add a `hint=` field when the output looks like a network problem (`hint=network-unreachable`) or a hit usage cap (`hint=usage-limit`):

```text
provider=claude status=ok exit=0 duration=2.03s reply=OK
provider=codex status=timeout duration=180.04s hint=network-unreachable
```

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
