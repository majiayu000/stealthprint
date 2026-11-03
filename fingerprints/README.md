# Fingerprint library

One JSON profile per model, written by `stealthprint profile` or seeded from case
measurements. Compare a new target offline:

```bash
stealthprint match fingerprints/<target>.json --library fingerprints
```

Profile fields (all optional except `model`):

| field | layer | content |
|---|---|---|
| `l1_delta` | L1 | API token delta per bundled probe (`T(base+probe) - T(base)`) |
| `segmentation` | L1b | streamed pieces per echoed text + key-token pattern |
| `errors` | L4b | error-text signature tags and leaked numeric limits |
| `vision` | L6b | image token cost per size + fitted `ceil(w/p)*ceil(h/p)+c` |
| `meta` | — | measurement date, gateway, source |

`match` verdicts: `match` only when every hard layer both profiles share agrees
fully (L1 24/24, all vision sizes, segmentation F1 = 1); `partial` when some do;
`different` otherwise. A `match` says the measured surfaces are identical, not that
weights, training data or vendor are proven identical.

Seed (2026-11-03): 23 named models on OpenCode Zen free tier and Go plan, plus the
two unresolved stealth targets `pulse-alpha-free` and `pixel-canary`. Gateway
counts include each gateway's own template constant; deltas cancel it, absolute
values do not compare across gateways. Measurements older than a model's next
silent update may be stale — re-profile before relying on a `different` verdict.
