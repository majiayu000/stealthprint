# Case study: omen-alpha

[English](case-omen-alpha.md) · [中文](case-omen-alpha.zh-CN.md)

A full fingerprint analysis of the anonymous model `omen-alpha` behind the
opencode zen gateway, reproduced end-to-end with this library's
(`stealthprint`) methodology. Raw measurements:
[case-omen-alpha-measurements.json](case-omen-alpha-measurements.json).

> **TL;DR:** `omen-alpha` on `https://opencode.ai/zen/go/v1` uses the **GLM-5
> tokenizer** (24/24, MAE 0.00). Same-gateway A/B against named `glm-5*` shows
> it is **not** GLM-5 / 5.1 (~200K context) and **not** GLM-5.3 flagship
> (text-only: rejects images and `video_url`). It matches **GLM-5.3-Flash**
> on tokenizer, native image+video, image/video token overhead, and
> `reasoning_effort=low|max` behavior, plus a fixed **+24 token stealth
> wrapper** vs the named `glm-5.3-flash` id (37 vs 13 on `"hi"`; 65 vs 41
> with a 64×64 image; 71 vs 47 with a tiny mp4). That path is a **different
> adapter**: omen accepts `reasoning_effort=none` (200); named Flash returns
> Zhipu **`[1210]`**. Verified context ≥ **969K**. 1×1 images still
> hallucinate. A later 24× 64×64 probe was **24/24 HTTP 200** (pt=65) with
> **16/24** visible answers literally `Red`/`Red.`; 8/24 empty content
> because `max_tokens=48` cut inside thinking. 0 serde rejects, 0 flagship
> “no image” errors.

---

## Verdicts (measured 2026-09-04)

| Layer | Finding | Confidence |
|---|---|---|
| 1 tokenizer | **GLM-5 vocab** (GLM-5 and GLM-5.3-Flash tokenizer.json are hash-identical). Two independent probe rounds: 24/24 exact, MAE 0.00 | High (24/24; o200k deviates systematically on discriminative probes) |
| 2 wrapper | **+36** vs local GLM tokenizer (`"hi"` 37−1). **+24** vs every named `glm-5*` on this gateway (`"hi"` 37 vs 13). Named GLM wrapper itself is +12; stealth prefix is the extra 24. Zero drift across lengths | High |
| 3 context | ≥ **969,653 tokens** all 200 OK; true ceiling not reached — bounded only by the gateway's 4.5 MiB body limit (≈1M). Rules out GLM-5 / 5.1 (`model_max_length` 202,752). Community's 500K claim is false | High (binary search + needles) |
| 3 needles | 3/3 at 595K and 3/3 at 833K, head/mid/tail exact | High |
| 4 serving stack | **Rust** Console Go adapter around the stealth id. Named `glm-5.3-flash` on the **same** gateway *does* leak Zhipu `[1210]/[1214]`. omen swallows `reasoning_effort=none` (HTTP 200) and does not escape `<\|begin_of_image\|>` (Δ=1 vs Flash Δ=7). `glm-5.2` `effort=none` returns a **GLM-5.3** error string (gateway copy-paste) | High |
| 6 vision | Native image like Flash, unlike `glm-5.3` (`does not support image inputs`, 8/8). 64×64 red: omen **24/24 HTTP 200**, **16/24** content `Red`/`Red.`, 8/24 empty (thinking truncated at `max_tokens=48`); Flash 8/8 HTTP 200 (3 explicit `Red`). Token overhead 65 vs 41 (= +24 wrapper). 1×1 still hallucinates. Earlier ~50% serde 400 on array content did **not** reproduce in this sample | High for “Flash-class vision”; medium for “mixed backends” |
| 6 video | Native `video_url` (data URI and https): omen and Flash both answer red; `glm-5.3` rejects `video_url`. Prompt tokens 71 vs 47 (data) and 4014 vs 3990 (https) — again **+24** | High |
| 7 SKU | **GLM-5.3-Flash-class weights + ~24-token stealth template + looser OpenAI adapter.** Not a straight alias of catalog `glm-5.3-flash`. Cannot prove byte-identical HF checkpoint | High for family/SKU class; not a weight hash |

### Against prior community conclusions

- "o200k median 1.00" — **refuted**. o200k deviates systematically on emoji ZWJ
  (21 vs 16), Chinese (18 vs 14), digits (17 vs 19), Thai (6 vs 16).
- "GLM-5 vocab + fixed +24 wrapper" — **both numbers are true in different
  frames**. vs local GLM tokenizer `"hi"` is +36; vs named `glm-5.3-flash` on
  this gateway the extra stealth prefix is **+24** (named GLM wrapper itself
  is +12).
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

### Same-gateway SKU A/B (2026-09-04, later session)

Same `/v1` catalog, same key, same request shapes. Named siblings:
`glm-5`, `glm-5.1`, `glm-5.2`, `glm-5.3`, `glm-5.3-flash`.

| Signal | omen-alpha | glm-5.3-flash | glm-5.3 | glm-5 / 5.1 |
|---|---|---|---|---|
| emoji_zwj Δ | 16 | 16 | 16 | 16 |
| `"hi"` prompt_tokens | 37 | 13 | 13 | 13 |
| 64×64 red image | 200, 16/24 content Red, pt=65 | 200, 3/8 content Red, pt=41 | 400 no image | n/a (text SKUs) |
| tiny mp4 `video_url` | 200, Red, pt=71 | 200, Red, pt=47 | 400 `video_url` unsupported | n/a |
| `reasoning_effort=none` | 200 (swallowed) | 400 `[1210]` thinking-only | 400 thinking-only | n/a |
| `<\|begin_of_image\|>` Δ | 1 (raw vocab) | 7 (escaped) | — | — |
| verified context | ≥969K | spec 1M | spec 1M | spec ~202K |

So: tokenizer match is **family**, not SKU. Vision + video + 1M context match
**Flash**, not flagship 5.3 and not 5 / 5.1. The stealth id is **not** a
rename of catalog `glm-5.3-flash`: wrapper, special-token hardening, and
`[1210]` leakage differ. `glm-5.2` `effort=none` even quotes a GLM-5.3
error string.

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
stealthprint vision --repeats 24        # L6 identical 64x64 mix probe
stealthprint video                      # L6 video_url shapes
stealthprint catalog --family glm       # L7 same-gateway A/B vs named siblings
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
   backends that reject the request before judging color accuracy. Repeat an
   identical 64×64 probe (`vision --repeats N`) to quantify mix, if any.
6. **Video modality**: tiny mp4 via `video_url` vs `type: video` vs mp4-as-image.
   Native video + matching image token slope is a Flash-class signal; flagship
   GLM-5.3 rejects both.
7. **Catalog A/B**: GET `/v1/models`, then the same cheap SKU card on the
   target and named siblings (`catalog --family glm`). Tokenizer match is
   family-level; vision/video/`reasoning_effort`/wrapper offset split SKUs.

### New findings in this case (not in prior case files)

- **SKU vs wrapper vs adapter**: GLM-5 tokenizer is shared across 5 / 5.1 /
  5.2 / 5.3 / Flash. omen-alpha matches **Flash** on image, video, and 1M-class
  context, plus a fixed **+24** stealth template vs named `glm-5.3-flash`. The
  stealth adapter is looser (swallows `effort=none`, does not escape special
  tokens). Named Flash on the same gateway still leaks Zhipu `[1210]/[1214]`.
- **Serde mix is time-varying, not a text-only SKU mix**: an earlier window
  rejected ~half of multimodal array-content requests (`untagged enum
  MessageContent`). A later 24× 64×64 red probe was 24/24 HTTP 200 (pt=65),
  16/24 with visible `Red`/`Red.` content, 8/24 empty content from thinking
  truncation. omen-alpha never returned the flagship `does not support image`
  string in that sample. Do not treat “red” inside reasoning as a color hit.
- **Dual error envelopes**: OpenAI-style (`invalid_request_error`) and
  Anthropic-style (`{"type":"error","error":{"type":"ModelError"}}`) mixed in
  one gateway.
- **Layered vision**: 1×1 solid red still hallucinates colors and invented
  dimensions (37×40 / 200×200). Judge color accuracy on ≥64px after the
  request is accepted.

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
pool detection, vision ground-truth protocol, wrapper-constant verification,
video modality probes, and same-gateway catalog A/B.

## License

MIT
