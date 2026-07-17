# Codex Network Gateway Component Absorption

This document records which external proxy/gateway project ideas are useful for
the local Codex network gateway design. It is not a production runbook.

## Position

The local gateway should stay a thin Codex-specific control plane:

```text
Codex / resource layer / tool layer
  -> Codex network gateway
     -> route decision and lease
        -> direct | current Clash | isolated mihomo | wrapper | optional backend
```

The gateway should not replace Clash Verge, system proxy settings, DNS, or TUN.
It should choose a route for one request class, return a per-process proxy
environment or localhost proxy URL, record evidence, and clean up lab leases.

## Absorbed Ideas

### GOST

Useful as a small forwarding and protocol-adaptation toolbox.

- Adopt now: localhost-only forwarding, proxy chain assembly, temporary ports,
  explicit process cleanup, hidden startup on Windows.
- Defer: TUN/TAP, DNS proxy, transparent proxy, reverse tunnel, global routing.
- Fit: good wrapper behind the Codex gateway when a tool needs a protocol bridge
  or a stable local endpoint.

### proxy-chain

Useful as a Node-based local HTTP proxy wrapper.

- Adopt now: upstream HTTP/SOCKS proxy wrapping, anonymized local proxy endpoint,
  connection/error statistics, explicit close semantics.
- Defer: custom response interception and complex authentication policy.
- Fit: browser/Playwright/Node callers that want a simple localhost HTTP proxy
  even when the upstream route is selected dynamically.

### easy_proxies

Useful as an optional proxy-pool backend and design reference.

- Adopt now: health-checked pool concept, stable per-node ports, region grouping,
  blacklist/release semantics, API-driven status.
- Defer: direct deployment as primary gateway, subscription ownership, WebUI.
- Fit: optional backend if local Clash/mihomo cannot provide enough per-node
  isolation or region-specific pool behavior.

### Resin

Useful as a high-end scheduling and observability reference.

- Adopt now: platform/account separation, sticky leases, passive plus active
  health checks, circuit breaker vocabulary, per-domain latency records.
- Defer: account identity extraction, business header parsing, massive proxy
  fleet management, broad auth model.
- Fit: design reference for future gateway scheduling and evidence tables.

## Local Safety Contract

- Lab-only experiments write under `_bridge/runtime/network_gateway_lab`.
- No system proxy, DNS, Clash subscription, Clash config, firewall, or TUN edits.
- No production Hub or resource-layer route is changed by this document.
- All helper processes must bind localhost, run hidden when possible, and be
  stopped by the command that created them.
- Codex/OpenAI conversation traffic is protected and not routed through lab
  experiments unless explicitly approved later.

## Current Experiment Path

1. Use `_bridge/clash_mihomo_control.py isolated-probe` to create a temporary
   mihomo process and verify a selected node without changing the main Clash
   node.
2. Use `_bridge/network_gateway_component_lab.py snapshot` to inspect available
   wrapper components and runtime versions.
3. Use `_bridge/network_gateway_component_lab.py proxy-chain-smoke` to prove
   that a localhost proxy wrapper can forward through a chosen upstream.
4. Keep GOST as a planned local-tool download until a concrete wrapper gap is
   proven.

## Design Boundary

The production gateway should expose route intents such as:

- `codex_chat`: protect stability; only use the primary approved route.
- `github`: prefer tested GitHub route and allow isolated lease if unstable.
- `package`: allow per-process proxy env and retry through isolated lease.
- `docs`: prefer direct or documented proxy depending on owner MCP behavior.
- `browser`: allow wrapper endpoint for Chrome/Playwright profile isolation.

The gateway should return compact machine-readable output:

- selected route kind
- proxy URL or environment variables
- lease id and expiry
- evidence ids
- risk class
- cleanup command

