# Case study: omen-alpha

[English](case-omen-alpha.md) · [中文](case-omen-alpha.zh-CN.md)

A full fingerprint analysis of the anonymous model `omen-alpha` behind the
opencode zen gateway, reproduced end-to-end with this library's
(`stealthprint`) methodology. Raw measurements:
[case-omen-alpha-measurements.json](case-omen-alpha-measurements.json).

> **TL;DR:** `omen-alpha` served via `https://opencode.ai/zen/go/v1` uses the
> **GLM-5 tokenizer** (24/24 probes exact match, MAE 0.00, vs o200k 10/24).
> Real verified context ≥ **969K tokens** (not the rumored 500K), bounded only
> by a 4.5 MiB gateway body limit. The serving stack is a **Rust** ("Console
> Go") OpenAI-compatible gateway, *not* the official Zhipu API (no
> `[1210]/[1214]` codes, accepts `developer` role). Vision is **heterogeneous**:
> ~half of multimodal requests hit a backend that rejects array content; on the
> accepting backend vision is real (correct colors, size-scaled token cost +18
> @64px → +102 @256px), but tiny 1x1 images get hallucinated answers (invented
> dimensions).

---

## Verdicts (measured 2026-09-04)

| Layer | Finding | Confidence |
|---|---|---|
| 1 tokenizer | **GLM-5 vocab** (GLM-5 and GLM-5.3-Flash tokenizer.json are hash-identical). Two independent probe rounds: 24/24 exact, MAE 0.00 | High (24/24; o200k deviates systematically on discriminative probes) |
| 2 wrapper | Fixed chat-template overhead **+36**, zero drift across 10 lengths | High |
| 3 context | ≥ **969,653 tokens** all 200 OK; true ceiling not reached — bounded only by the gateway's 4.5 MiB body limit (≈1M). Community's 500K claim is false | High (binary search + needles) |
| 3 needles | 3/3 at 595K and 3/3 at 833K, head/mid/tail exact | High |
| 4 serving stack | **Rust** (verbatim serde errors), upstream name "Console Go", OpenAI-compatible schema (incl. `developer` role). **Not** the official Zhipu API stack: no `[1210]/[1214]` numeric codes; `temperature: 2.0` accepted | High (byte-level error samples) |
| 6 vision | **Partially working, heterogeneous backends.** Multimodal array content fails ~half the time (serde `untagged enum MessageContent`) → at least two stacks behind one model id. When the request passes, vision is real: correct colors, size-scaled overhead (+18 ≤64px, +102 @256px). But 1x1 images get wrong answers with invented dimensions (37x40, 200x200) | Medium-high (consistent on the accepting side; rejection rate/broken-backend mix random) |

### Against prior community conclusions

- "o200k median 1.00" — **refuted**. o200k deviates systematically on emoji ZWJ
  (21 vs 16), Chinese (18 vs 14), digits (17 vs 19), Thai (6 vs 16).
- "GLM-5 vocab + fixed +24 wrapper" — vocab right, **wrapper value wrong**
  (measured constant +36 here; possibly a different entry/template version).
- "500K context / 128K output" — context floor is at least 969K; output cap
  unverified (no request-level validation; `max_tokens: 999999` accepted).

### Discriminative probes (API delta vs candidate vocabs)

| probe | API Δ | GLM-5 | o200k | dots3 | qwen3 | minimax | deepseek | llama3 |
|---|---|---|---|---|---|---|---|---|
| emoji_zwj | **16** | 16 | 21 | 20 | 20 | 20 | 20 | 28 |
| digits | **19** | 19 | 17 | 32 | 32 | 17 | 17 | 17 |
| zh_long | **14** | 14 | 18 | 15 | 15 | 13 | 14 | 21 |
| thai | **16** | 16 | 6 | — | — | — | — | — |
| ja | **7** | 7 | 7 | 6 | 6 | 6 | 10 | 7 |
| ko | **7** | 7 | 4 | 5 | 5 | 4 | 7 | 4 |

Full raw data: [`case-omen-alpha-measurements.json`](case-omen-alpha-measurements.json).

---

## Reproduce

Works against any OpenAI-compatible endpoint (not limited to omen-alpha):

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"

export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
export STEALTHPRINT_MODEL="omen-alpha"
export STEALTHPRINT_API_KEY="sk-..."    # or in ~/.local/share/opencode/auth.json

./fetch_tokenizers.sh                   # local comparison vocabs (HF open files, zero API cost)

stealthprint tokenizer                  # L1 — cheapest, hardest evidence; run first
stealthprint wrapper                    # L2
stealthprint context                    # L3 — big requests, slow
stealthprint errors                     # L4
stealthprint vision                     # L6
```

Custom probes: edit `probes.json` (`base` + `probes` array); the differential
`Δ = T(base+probe) − T(base)` cancels the chat-template constant.

### Cost

All calls `max_tokens: 1` (except needles); 60+ calls this session, gateway
billed 0. All comparison-side token counts are local files — **no** Zhipu /
OpenAI / Xiaohongshu accounts needed.

---

## Methodology (extends the Ox Alpha case playbook)

1. **Tokenizer differential**: fixed base + probes, subtract
   `usage.prompt_tokens` across two requests to isolate the model's own
   tokenizer count, compare against local open `tokenizer.json` `encode()`.
   Discriminative probes must cover: English pangram, long Chinese, code
   indentation, emoji ZWJ, rare Unicode, digit grouping, Thai/Russian/French.
2. **Wrapper constant**: `prompt_tokens` minus local raw count across prompt
   lengths; a constant delta = gateway template. Same model via a different
   entry may change this number; the vocab differential does not.
3. **Context probes**: binary-search input length toward the ceiling (watch
   for the gateway body limit tripping first), then hide needles at
   head/mid/tail to verify true recall. The rejection text at the boundary is
   itself a fingerprint.
4. **Error envelope family**: malformed parameters (type errors, out of range,
   wrong role). Look at numeric vs string codes, serde/Java error text,
   validation language. This layer identifies the **serving stack**, not the
   weights file.
5. **Vision ground truth**: solid-color Q&A + image token-overhead slope
   across sizes. Size-scaled overhead + correct colors = real encoder;
   constant overhead or wrong colors = placeholder/fake pipeline. Filter out
   backends that reject the request before judging color accuracy.

### New findings in this case (not in prior case files)

- **Heterogeneous backend pool**: behind one model id, multimodal array
  content is rejected by serde ~half the time (`untagged enum MessageContent`);
  the same request passes on retry. Suspect multiple upstream versions behind
  the gateway.
- **Dual error envelopes**: OpenAI-style (`invalid_request_error`) and
  Anthropic-style (`{"type":"error","error":{"type":"ModelError"}}`) mixed in
  one gateway.
- **Layered vision**: backends that accept images do real vision (correct
  colors, resolution-scaled tokens); on backends that silently fail, the model
  answers from the prompt alone — 1x1 solid red described as green/yellow with
  invented 37x40 / 200x200 dimensions. Vision verdicts must first filter out
  failing backends, then judge color accuracy.

### Evidence strength tiers

- Species-level (conclude directly): tokenizer differential 24/24
- Stack-level (identifies the service, not the weights): verbatim serde
  errors, `developer` role, dual envelopes
- Behavior-level (exclusion only): needle recall, color ground truth, token slope

### Do not trust on its own

"Who are you" self-descriptions, injected system prompts, censorship probes,
emoji density. Stealth models' self-introductions are frequently bait.

---

## Prior art

Methodology extends: `iSimplifyMe/tokenizer-fingerprint` (95 probes + vocab
fetching), `LuD1161/ox-alpha-identification-public` (44 discrimination strings),
`unclecode/modelprint` (browser probes). This case adds: heterogeneous-backend
pool detection, vision ground-truth protocol, wrapper-constant verification.

## License

MIT
