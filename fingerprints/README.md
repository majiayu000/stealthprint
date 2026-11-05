# Fingerprint library

One JSON profile per model, written by `stealthprint profile` or seeded from case
measurements. Compare a new target offline:

```bash
stealthprint match fingerprints/<target>.json --library fingerprints
```

Profile fields (all optional except `model`):

| field | layer | content |
|---|---|---|
| `count_integrity` | — | `count-check` result; `untrusted` removes L1 and vision from every comparison with this profile |
| `tokenizer_candidates` | — | local tokenizers the evidence allows: every L1 probe under trusted counts, and truncation when it has at least 30 cuts |
| `truncation` | L1c | cuts per bundled text as `[max_tokens, cut]`, plus streamed boundaries when chunks are per-token |
| `l1_delta` | L1 | API token delta per bundled probe (`T(base+probe) - T(base)`) |
| `segmentation` | L1b | streamed pieces per echoed text + key-token pattern |
| `errors` | L4b | error-text signature tags and leaked numeric limits |
| `vision` | L6b | image token cost per size + fitted `ceil(w/p)*ceil(h/p)+c` |
| `meta` | — | measurement date, gateway, source |

`match` verdicts: `match` only when every hard layer both profiles share agrees
fully (L1 24/24, all vision sizes, segmentation F1 = 1); `partial` when some do;
`different` otherwise. A cut off the other model's streamed boundaries counts as a failed
hard layer. The absence of such a conflict does not count as agreement: cuts can refute a
tokenizer but not confirm one.
With no hard layer, `tokenizer_match` means the tokenizer candidates overlap, which an open
tokenizer allows for unrelated models; disjoint non-empty candidates are `different`; `no_overlap`
means nothing is usable. An empty candidate list means no local tokenizer fits, which says
nothing about the other model. Vision counts of
zero at every size and non-per-token streams are not evidence. 2026-11-05: every profile except
`pixel-canary` carries a `count_integrity` result; only `crouton-alpha-free` is `untrusted`.
`pixel-canary` is on another gateway and stays unchecked. The GPT lines report zero usage for
some texts: L1 retries it and records a failure if it stays zero, so those profiles have
fewer than 24 probes. A `match` says the measured surfaces are identical, not that
weights, training data or vendor are proven identical.

Seed (2026-11-03): 23 named models on OpenCode Zen free tier and Go plan, plus the
two unresolved stealth targets `pulse-alpha-free` and `pixel-canary`. Gateway
counts include each gateway's own template constant; deltas cancel it, absolute
values do not compare across gateways. Measurements older than a model's next
silent update may be stale — re-profile before relying on a `different` verdict.
