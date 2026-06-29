# Operations

## Logs

Default log file:

```text
~/.local/state/kappa/kappa.log
```

Each run records provider start, success, failure, timeout, or skipped
overlapping execution.

```bash
tail -f ~/.local/state/kappa/kappa.log     # follow live
tail -n 20 ~/.local/state/kappa/kappa.log  # last 20 lines
```

A successful run logs the model's (truncated) reply, e.g. `status=ok ...
reply=OK`, so you can confirm the prompt actually landed and warmed the window
rather than failing silently. If a provider has a `status_command`, the rolling
usage window is logged right after as a `status=window` line. Failures and
timeouts add a `hint=` field when the output looks like a network problem
(`hint=network-unreachable`) or a hit usage cap (`hint=usage-limit`):

```text
provider=claude status=ok exit=0 duration=2.17s reply=OK
provider=claude status=window detail='... Current session: 53% used · resets Jun 27, 11:39pm (America/New_York)'
provider=codex status=timeout duration=180.04s hint=network-unreachable
```

## Log rotation

The sample `logrotate.kappa` config keeps logs bounded for a root-owned VPS
install. It rotates weekly and keeps two compressed archives.

```bash
sudo cp /opt/kappa/logrotate.kappa /etc/logrotate.d/kappa
sudo chmod 644 /etc/logrotate.d/kappa
sudo logrotate -d /etc/logrotate.d/kappa   # dry-run: check logrotate accepts it
sudo logrotate /etc/logrotate.d/kappa      # apply once now, if desired
```

## Locking

`kappa` uses a non-blocking lock file so overlapping runs do not stack up.

Default lock file:

```text
~/.local/state/kappa/kappa.lock
```
