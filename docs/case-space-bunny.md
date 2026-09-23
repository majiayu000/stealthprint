# Case study: space-bunny-free

[English](case-space-bunny.md) · [中文](case-space-bunny.zh-CN.md)

Fingerprint analysis of `space-bunny-free`, an anonymous `-free` model listed
on the opencode zen gateway (both the public `/zen/v1` and Go `/zen/go/v1`
lines), performed end-to-end with this toolkit (`stealthprint`). Raw
measurements: [case-space-bunny-measurements.json](case-space-bunny-measurements.json).
Unlike the two earlier cases, the free tier is now client-gated (403
`FreeTierError` on bare API calls); every datapoint below comes from the Go
line behind an active Go subscription. The model is **not listed on
OpenRouter** (456-model catalog checked).

> **TL;DR:** `space-bunny-free` on `https://opencode.ai/zen/go/v1` uses the
> **MiniMax tokenizer** (24/24, MAE 0.00) and is **not Kimi** (13/24) nor
> GLM (7/24). Same-gateway A/B: its 24-probe delta vector is **identical** to
> both `minimax-m3` and `minimax-m2.5` (which are themselves identical —
> the MiniMax vocab is shared across generations, so tokenizer evidence is
> family-level). The Kimi hypothesis is separately refuted: `kimi-k3`,
> `kimi-k2.7-code`, `kimi-k2.6` share one signature that differs from
> space-bunny on 14/24 probes. Stealth wrapper: fixed **+143 token** chat
> template, zero drift from 10¹ to 10⁶ tokens. Context ≥ **1M tokens**
> (100K/204.6K/450K/1M ladder all 200 OK, `pt = local count + 143` exact),
> which **rules out the m2.5 generation** (its endpoint caps at 204,800);
> 200K-depth three-distinct-needle retrieval 3/3 character-exact. Vision is
> adapter-class: images accepted, overhead **+34 constant regardless of
> resolution** (1×1 == 64×64), 64×64 colors 8/8 correct, degenerate 1×1
> hallucinates 1/4. The backend pool is **homogeneous** (14/14 identical
> payloads → identical status/pt/shape — no omen-style 50/50 mix). Asked who
> it is, it improvises **"I'm ChatGPT, made by OpenAI"** because the stealth
> template provides no identity (its own thinking leaks this). Effective
> knowledge ≥ 2025-11 (knows Claude Opus 4.5's month, which `minimax-m3`
> does not) and < mid-2026 → consistent with a **post-M3 or M3-refresh
> stealth preview** (inference, medium confidence).

---

## Verdicts (measured 2026-09-24)

| Layer | Finding | Confidence |
|---|---|---|
| 1 tokenizer | **MiniMax vocab**, 24/24 exact, MAE 0.00 vs local M1 tokenizer.json (vocab shared across MiniMax generations). Kimi 13/24 MAE 1.62; GLM 7/24 (worst); llama4 13/24 | High |
| 7 catalog A/B | Delta vector **identical** to `minimax-m3` and `minimax-m2.5` on all 24 probes. Kimi trio (k3/k2.7-code/k2.6) mutually identical, differs on 14/24. Pipeline cross-validated: kimi-k3 API deltas vs locally built Kimi K2 tiktoken = 23/24 | High |
| 2 wrapper | **+143** fixed chat template, zero drift at 16→1M tokens (5 magnitudes; one +14 outlier at the 810K needle, BPE boundary effect). base_pt: space-bunny 159 / m3 192 / m2.5 57 — three different templates | High |
| 3 context | ≥ **1M tokens** (ladder 100K/204.6K/450K/1M all 200 OK; `pt = local + 143` at every step). **Rules out m2.5-generation** (204,800 endpoint cap, quoted in its own error text). Upper bound not reached | High |
| 3 needles | 200K depth, three **distinct** codes at 1/3/2/3/end: 3/3 character-exact in order. (810K needle was same-second-duplicate-codes — weak, but depth read succeeded) | High (200K) |
| 4 serving stack | Normalized error envelope: every malformed payload → uniform `[invalid_request_error] invalid request`, leaks nothing. Contrast m3 leaks MiniMax native codes (`(2013)`, `model[MiniMax-M3] does not support max tokens > 524288`), m2.5 a translation layer, kimi pydantic text. Four distinct stacks; space-bunny's is shielded | High |
| 6 vision | Adapter-class: +34 pt overhead **constant across resolutions** (1×1 == 64×64 — same fake-ViT signature class as omen's +18). 64×64 red 4/4, blue 4/4 correct; 1×1 red 3/4 "Red" + 1/4 "Black". m3 accepts images with a different signature (−1/+7); m2.5 has **no vision route** (404) | High |
| pool | 14 identical payloads (7 text `temperature=2.0` + 7 image): 14/14 HTTP 200, pt/shape/`name` identical → **homogeneous single stack** (no omen-style 50/50 serde split) | High |
| identity | Self-reports **ChatGPT/OpenAI**; thinking leaks "no model identity provided… need adhere system/developer". `name: "Space Bunny"` field on every message (unique on gateway). Telegram-style M2-family clipped thinking | High (behavior), n/a (weights) |
| knowledge | ≥2025-11 (Opus 4.5 month; m3 doesn't know it), knows GPT-5 exact date (m2.5 doesn't), unknown mid-2026 (Fable 5, Kimi K3, GLM-5.3). Self-claimed "June 2024" cutoff is improvised | High (facts), medium (inference) |

### Generation (inference, medium confidence)

Tokenizer proves family (MiniMax) but not generation. The generation call
assembles: context ≥1M (m3-class; kills m2.5), effective knowledge ≥2025-11
(fresher than m3's realized coverage), M2-family telegram thinking style,
+143 template vs m3's +178/m2.5's +37 — a distinct serving path. Reads as a
**post-M3 or M3-refresh stealth preview**, following the gateway's naming
pattern (`omen-alpha` = GLM-5.3-Flash, `union-alpha` = Unbiased Pareto,
`space-bunny` = MiniMax). Cannot prove checkpoint identity.

---

## Reproduce

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"

export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
export STEALTHPRINT_MODEL="space-bunny-free"
export STEALTHPRINT_API_KEY="oc_sk-..."   # Go subscription required; free tier is client-gated

./fetch_tokenizers.sh                    # 9 vocabs; Kimi needs tiktoken.model (see below)

stealthprint tokenizer                   # L1 — settles family in ~25 calls
stealthprint wrapper                     # L2
stealthprint errors                      # L4
stealthprint catalog --family kimi       # L7 A/B vs named siblings
```

Kimi comparison vocab: Moonshot publishes no `tokenizer.json` (K2/K2.5/K3
ship only `tiktoken.model` + `tokenization_kimi.py`). Build the candidate
with tiktoken directly:

```python
from tiktoken import Encoding
from tiktoken.load import load_tiktoken_bpe
enc = Encoding(name="kimi-k2", pat_str=KIMI_PAT_STR,          # from tokenization_kimi.py
               mergeable_ranks=load_tiktoken_bpe("tok/kimi-k2.tiktoken"),
               special_tokens={})
```

Validated in-session: kimi-k3 API deltas match this construction 23/24.

### Cost

~290 calls this session (≈125 A/B tokenizer probes, ≈35 gap-fill incl. four
1M-class payloads ≈1.4 MB each). Comparison-side counts are local files.

---

## Methodology notes (extends omen/union playbooks)

1. **Same-gateway A/B upgrades L1 from file-match to live-endpoint match**:
   identical delta vectors on named siblings (`minimax-m3`/`m2.5`) prove the
   whole counting path, not just the vocab file.
2. **Context-limit-as-generation-fingerprint**: 204,800 vs 1M is a hard,
   cheap split between m2.5-era and m3-era serving (one 450K probe settles
   it). The ladder doubles as wrapper-constancy data (`pt − local` per rung).
3. **Knowledge ladder with dated events** (R1 2025-01 → GPT-5 2025-08 →
   Opus 4.5 2025-11 → mid-2026 unknowns): separates m2.5/m3/space-bunny
   cleanly; self-*claimed* cutoffs are noise (all three misreported).
4. **Pool homogeneity probe**: N identical payloads, compare status/pt/shape
   variance — here 14/14 identical, the anti-omen result.
5. **Bug log**: lambda-timestamp needle generators must add randomness —
   same-second codes collided twice in this session before the fixed rerun.

## License

MIT
