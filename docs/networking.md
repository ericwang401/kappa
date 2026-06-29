# Routing a blocked provider

Some provider endpoints are unreachable from datacenter/VPS IPs. Codex's
ChatGPT-login endpoint (`chatgpt.com`) in particular is fronted by Cloudflare,
which resets connections from hosting-provider IP ranges and flags the Linux
CLI's TLS fingerprint as a bot — so the CLI is installed and logged in, yet every
run resets or times out while `api.anthropic.com` (Claude) stays reachable. This
is an IP/TLS reputation block, not a rate limit, so lowering the schedule
frequency does not help. `kappa doctor` surfaces it via `check_url`:

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
  a residential machine (e.g. a Tailscale node) and point the provider's `env` at
  it (`ALL_PROXY = "socks5://127.0.0.1:1055"`). SOCKS proxies are used at run
  time but not probed by `doctor` (the probe needs an HTTP/HTTPS proxy).
- **Wrap the command**, e.g. `command = ["proxychains4", "codex", "exec", ...]`.
- **Run that provider's warmup from a residential machine** (laptop, home server)
  on its own schedule, and leave only the reachable providers on the VPS.

Using an API key instead of the ChatGPT login avoids `chatgpt.com` entirely, but
it bills per token and does not warm the subscription's usage window, so it
defeats the purpose of a warmup ping.
