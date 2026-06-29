# Scheduling

kappa does not run a daemon — a scheduler starts the script at fixed times. Use a
**systemd timer** (recommended) or **cron**.

## Default schedule

```text
7:30 AM, 12:35 PM, 5:40 PM, 10:45 PM, 2:30 AM
```

The usage window does not reset on a fixed clock; it resets roughly five hours
after the window's first message lands. The daytime fire times are spaced a
little over five hours apart (5h5m) so each one lands just after the previous
window resets instead of just before it, rather than at an exact five-hour mark.

## The timezone trap

The fire times only matter if the scheduler interprets them in the intended zone,
and this is the easiest thing to get wrong. **Debian/Ubuntu's cron ignores
`CRON_TZ` entirely** — not just in a per-user crontab (`crontab -e`) but in
`/etc/cron.d` too. Debian's cron only ever runs in the host's system timezone. On
a UTC host that silently shifts every fire time by 4–5 hours, so a window you
expected at 7:30 AM ET actually anchors at 3:30 AM ET. (Confirm what actually
happened by comparing `kappa.log` timestamps, or `journalctl`, against the
schedule.)

So there are two real options: a **systemd timer** (keeps the host on UTC) or
**cron with the host timezone set** to your zone.

## Run as the right user

The warmup needs the provider CLIs on `PATH` *and* their logged-in credentials
(`~/.claude`, `~/.codex`). Both resolve from the running user's home, so the
service should run as the user who installed and logged into the CLIs — usually
not root. A unit with no `User=` runs as root, whose home lacks both, which
surfaces as `command not found` in `journalctl`.

- **systemd:** pass `--user <name>` to `kappa systemd` (adds `User=`; systemd
  then sets `$HOME`/`$USER` from passwd).
- **cron:** install under `/etc/cron.d` with `kappa cron --cron-d --user <name>`,
  or just use that user's own crontab.

## Option A — systemd timer (recommended)

systemd timers honor a timezone suffix on `OnCalendar` and handle DST, so the
host clock can stay on UTC (the usual best practice) while fire times stay
anchored to your zone. `kappa systemd` prints a service + timer; `--write`
installs them:

```bash
# run the warmup as user "eric" (omit --user to run as root)
sudo python3 kappa.py systemd --user eric --write   # writes /etc/systemd/system/kappa.{service,timer}
sudo systemctl daemon-reload
sudo systemctl enable --now kappa.timer
systemctl list-timers kappa.timer       # verify the next fire time
journalctl -u kappa.service -n 20       # run history
```

The timer's `OnCalendar` lines carry the zone from `kappa.toml`'s `timezone`:

```ini
[Timer]
OnCalendar=*-*-* 07:30:00 America/New_York
...
```

Re-running `--write` overwrites the existing unit files in place; just
`daemon-reload` afterwards. An already-enabled timer stays enabled.

## Option B — cron with the host timezone set

Since Debian/Ubuntu cron has no working `CRON_TZ`, the only way to control its
zone is the host clock. Set it, then install the entries:

```bash
sudo timedatectl set-timezone America/New_York
sudo systemctl restart cron
python3 kappa.py cron        # entries run in the host TZ
crontab -e                   # paste them
```

`kappa cron` deliberately does **not** emit a bare `CRON_TZ` line (it would be a
no-op here). `kappa cron --cron-d` emits `/etc/cron.d` format with `CRON_TZ` for
crons that *do* support it (e.g. Vixie cron on RHEL/Fedora) — but not
Debian/Ubuntu.

## Uninstall

### systemd timer

```bash
sudo systemctl disable --now kappa.timer        # stop and remove from boot
sudo systemctl stop kappa.service               # stop a run in progress, if any
sudo rm /etc/systemd/system/kappa.service /etc/systemd/system/kappa.timer
sudo systemctl daemon-reload
sudo systemctl reset-failed kappa.service       # clear any lingering failed state
```

### cron

- Per-user crontab: `crontab -e` and delete the kappa lines.
- `/etc/cron.d`: `sudo rm /etc/cron.d/kappa`.

### Leftover files (optional)

These are not removed by the steps above:

```bash
sudo rm /etc/logrotate.d/kappa            # if you installed log rotation
rm -rf ~/.local/state/kappa               # log + lock files (run as the warmup user)
sudo rm -rf /opt/kappa                    # the deployed script + config, if you used that path
```
