# Network Dependency Baseline

This workspace keeps common networking dependencies ready so Codex can diagnose
and work around HTTP, proxy, TLS, and runtime-specific issues without first
needing to research or install basics during an incident.

## Python Runtime

Installed into the current Codex Python runtime:

- `requests`
- `httpx`
- `aiohttp`
- `certifi`
- `truststore`
- `PySocks`
- `requests-toolbelt`
- `requests-cache`
- `dnspython`
- `tenacity`
- `h2`

## Node Runtime

Installed project-locally under `_tools/network_toolkit`:

- `undici`
- `proxy-agent`
- `https-proxy-agent`
- `socks-proxy-agent`
- `pac-proxy-agent`
- `proxy-from-env`
- `hpagent`
- `axios`
- `got`

## Use

Run the read-only network doctor first:

```powershell
python .\_bridge\network_doctor.py
```

When a Node script needs these dependencies, run it from
`_tools\network_toolkit` or add that `node_modules` directory to the Node module
search path for the specific process.
