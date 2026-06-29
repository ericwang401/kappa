# Configuration

Everything kappa needs lives in `kappa.toml`.

## Prompt

The default prompt is intentionally minimal — just enough to land a message and
start the usage window:

```toml
prompt = "Reply exactly: OK"
```

## Provider commands

The default Claude command uses the `haiku` alias with low effort and a small
per-run budget cap. The default Codex command does not pin a model name, because
Codex's lower-cost model IDs change over time; it only requests low reasoning
effort and low verbosity.

If a scheduler cannot find a provider command, update `path` or use absolute
command paths in each provider's `command` array. The default `path` includes
common Linux locations plus `/opt/homebrew/bin` for local macOS testing.

> Note: `path` is `~`-relative. Under systemd/cron the `~` expands to the
> *running user's* home, so run the service as the user who installed the CLIs
> (see [scheduling](scheduling.md)).

### Optional per-provider keys

Each `[providers.*]` table accepts a few optional keys beyond `enabled` and
`command`:

- `timeout_seconds` — overrides the global `timeout_seconds` for just this
  provider. Codex reasoning can run well past the global 90s cap, and a run
  killed mid-flight never lands a message (so its usage window never starts), so
  the default config gives Codex `180`.
- `check_url` — a URL that `kappa doctor` probes to confirm the provider's API
  host is reachable from this machine. Any HTTP response (even 401/403/404)
  counts as reachable; a connection reset or refusal is reported as a failure.
- `env` — a table of environment variables applied to only this provider's run.
  Use it to route one provider through a proxy or exit node (see
  [networking](networking.md)) without affecting the others.
- `status_command` — a command run after a successful warmup whose output is
  logged as a `status=window` line, so the log captures the rolling usage
  window. It is a read-only status query that does not consume the window it
  reports on; failures are ignored and never affect the warmup result.
  - **Claude** uses `claude /usage`, which prints e.g. `Current session: 53%
    used · resets Jun 27, 11:39pm`.
  - **Codex** has no scriptable status flag (`/status` is interactive-only), so
    kappa ships [`codex_usage.py`](../codex_usage.py), which drives Codex's own
    `account/rateLimits/read` app-server RPC headlessly and prints both windows,
    e.g. `5h: 2% used, resets 2026-06-28 06:55 UTC | weekly: 1% used, resets
    2026-07-03 07:30 UTC`. Point the configured path at the deployed copy
    (default `/opt/kappa/codex_usage.py`). Like the warmup it reaches
    `chatgpt.com`, so it only runs once Codex connectivity works.

## Timezone

The `timezone` key (default `America/New_York`) sets the zone used for two things
only: the timestamps written to `kappa.log`, and the `CRON_TZ=` line emitted by
`kappa cron`. **It does not control when the scheduler actually fires** — see the
[timezone trap](scheduling.md#the-timezone-trap). Set it to whatever zone you
want the log timestamps to read in; matching it to the host timezone keeps the
log and the schedule consistent.

## Check setup

```bash
python3 kappa.py doctor
```

This checks the config, timezone, log directory, lock directory, and enabled
provider commands. For any provider with a `check_url`, it also probes whether
that API host is reachable from this machine — the quickest way to spot a
provider whose CLI is installed and logged in but whose endpoint is blocked from
this network (see [networking](networking.md)).
