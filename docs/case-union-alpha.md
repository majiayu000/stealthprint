# Case study: union-alpha

[English](case-union-alpha.md) · [中文](case-union-alpha.zh-CN.md)

Fingerprint analysis of `stealth/union-alpha`, the anonymous free-preview model listed on OpenRouter on 2026-09-16 (price 0), performed end-to-end with this toolkit (`stealthprint`). OpenCode Zen (public and Go lines both list `union-alpha`) returned HTTP 500 for inference throughout the measurement window, so every datapoint below comes from the OpenRouter entry.

> **TL;DR:** `stealth/union-alpha` uses the **Llama-3 tokenizer (128K)** (15 discriminating probes × 12 repeats, zero deviation; reconfirmed by a mixed-backend-aware rerun) behind a **+16~17 token fixed template** (the constant drifts ±1 across sessions). It has a **real vision encoder**: red/blue ground truth passes both ways (red 5/6, blue 3/4), small images cost a flat 24 tokens (fixed patch count), and 256×256→108 / 512×512→369 scale ~linearly with pixels. **No native video**. Needle recall at 187K tokens is 3/3 exact. **Tool-calling fingerprint**: `tool_choice="none"` is completely ignored; parallel calls are the default. At least **two heterogeneous backends are load-mixed**: the majority path (llama3 + 16/17, ~70%) and an unstable-counting secondary path (~30%), both reported by OpenRouter as `provider: "Stealth"`. Excluded: every GLM, Qwen3, DeepSeek, dots3, MiniMax, o200k, Llama 4 (new 201K vocab), **Xiaomi MiMo-V2.5 (≡Qwen vocab, transitive exclusion)**, and **Meta's Muse family (muse-glimmer-30b measured = llama4 vocab 24/24, family-level inference)**. Of the community guesses, MiniMax M3.1 is directly excluded by tokenizer.

---

## Findings (measured 2026-09-17, OpenRouter entry)

| Layer | Finding | Confidence |
|---|---|---|
| 1 Tokenizer | **Llama-3 vocab.** All 15 discriminating probe deltas exactly equal the local llama3 tokenizer.json; two independent rounds (4-rep and 12-rep) plus a `--repeats` mixed-aware rerun (llama3 3/4 exact, all 9 other candidates 0/4) agree | High (species-level) |
| 1b Completion-side | echo repeats 7/8 character-exact (generation is real, not a placeholder relay); **hidden reasoning billing**: en_pangram echo is exact yet bills 34 extra tokens, zh_long generates 120 tokens of entirely invisible thinking (Chinese long text triggers the thinking path); completion-side vocab discrimination is weaker than input-side | High (phenomenon) |
| 2 Wrapper | **+17** (12-rep round) / **+16** (rerun) vs local llama3 — the template constant drifts between sessions; multi-turn accumulation deltas are non-constant | High |
| 3 Context | Advertised 262,144 (OpenRouter config); needle test at 187,039 tokens: **head/mid/tail 3/3 exact recall**, ceiling not reached | High (floor 187K) |
| 4 Service stack | `developer` role accepted; `reasoning_effort=none` swallowed (200); out-of-range `temperature` accepted; `role:"wizard"` and mp4-as-image both trigger upstream 502 (OpenRouter wraps as HTTP 200 + error object); `max_tokens` not enforced upstream | High |
| 5 Tools | **`tool_choice` semantics missing**: none/auto/required behave identically — "none" still returns `finish=tool_calls`; **parallel calls by default** (get_weather + get_time in one response, exact args); schema overhead 168 tokens | High |
| 6 Vision | **Real encoder.** 64×64 red 5/6 "Red", blue 3/4 "Blue" (discriminating, not red-biased); 1×1 and 64×64 both 24 tokens (fixed patches); 256×256 = 108, 512×512 = 369 (~linear in pixels) | High |
| 6 Video | **None.** `video_url` filtered at OpenRouter routing (404, `Filter by Input Video`); `type:video` placeholder-handled (pt=28, red mp4 answered "Blue"); mp4 in `image_url` → upstream 502 | High |
| Mixing | Same-shape requests split across prompt_tokens paths (base text: 33 × 17, 15 × 6, 21 × 1 over 24 calls); multi-turn deltas go **negative** (turn 3→4: 46→40 — impossible on monotonic content = path switch, so the turns probe doubles as a mixing detector); both paths report `provider: "Stealth"`; deepened (round 2, 09-17): **the secondary path is not a second model** — behavioral split shows both paths identical in echo fidelity, completion-token modes, and hidden-reasoning billing, while the secondary counts (base 15) sit below every one of the 11 candidates' raw count (all 16), producible by no known vocab → a single llama3-family backend behind a front re-counting channel, with routing weights drifting over time | High (phenomenon) / Medium-high (secondary = counting channel) |

### Mixed backends

The secondary (low) path's counts are **unstable across rounds** (fr probe 19→29, ru 34→30), match no local candidate vocab, and are not "majority + constant". Differential probes must pair within one path (majority mode; now codified in the library as `tokenizer --repeats`).

**Deep dive (2026-09-17 round 2: routing census + absolute-count pairing + echo behavioral split)**:

- **Majority path re-confirmed**: 4-probe deltas (zh 21 / fr 16 / digits 17 / emoji 28) all exactly llama3, identical to round 1.
- **Secondary-path counts do not close**: delta pairing gives a lone qwen3 hit (zh 15) vs a lone glm5 hit (fr 14) — mutually contradictory — and digits 33 / emoji 33/8 match zero of 11 candidates. Absolute pairing is harder still: **every one of the 11 candidates counts the base text at exactly 16; the secondary path reports 15 — no vocab can produce it**. The absolute values hop between "glm5 minus 1" (base/zh/fr: 15/30/29, then fails digits) and qwen3-line raw counts (digits 48) **across epochs** — the counter's own vocab drifts.
- **Ratio drift**: this round's base histogram is 15×8 / 33×5 — the secondary channel overtook the majority (round 1: 33×17 / 15×6). Routing weights follow time/load, not a fixed 70/30.
- **Behavioral split (decisive)**: echo probes ×N grouped by pt path — the low path scores 8/8 exact echoes, its completion_tokens modes (14, 21) equal the high path's, and the same hidden-reasoning long tails appear. A glm5/qwen3-line model cannot be generation-side identical to a llama3-line one (the ct modes are themselves a counting-vocab fingerprint).
- **Revised verdict**: round 1's "different backend/counter" attribution narrows to **counter** — a single llama3-family backend behind a fusion-router-style front layer that routes between **counting/billing channels**, not between models. The "dispatches to different models" hypothesis is rejected on behavior; "a front rewrite/re-count layer exists" is confirmed on counts (a half-confirmation of the fusion-router hypothesis).

### Discriminating probes (majority-path deltas vs vocabs)

| probe | API Δ | llama3 | glm5 | o200k | qwen3 | minimax | llama4 | mimo-v2.5 |
|---|---|---|---|---|---|---|---|---|
| emoji_zwj | **28** | 28 | 16 | 21 | 20 | 20 | 36 | ≠ |
| zh_long | **21** | 21 | 14 | 18 | 15 | 13 | 31 | ≠ |
| digits | **17** | 17 | 19 | 17 | 32 | 17 | 32 | ≠ |
| fr | **16** | 16 | 14 | 10 | 15 | 9 | 28 | ≠ |
| ru | **16** | 16 | 11 | 13 | 16 | 15 | 27 | ≠ |
| ja | **7** | 7 | 7 | 7 | 6 | 6 | 26 | ≠ |
| ko | **4** | 4 | 7 | 4 | 5 | 4 | — | ≠ |

(llama4 column measured from Llama-4-Scout, vocab 201,135. mimo-v2.5's vocab equals qwen3 row-for-row, not repeated.)

### Community guesses and further candidates

- "MiniMax M3.1" — **excluded by tokenizer** (MiniMax-M1 vocab misses every row; M3.1 presumably inherits M-series vocab).
- "Kimi K3.1" — moonshot publishes no tokenizer.json (no local comparison possible); K-series has used its own 163K vocab, which contradicts a 128K llama3 match (medium confidence, indirect).
- "Xiaomi MiMo" — **excluded** (2026-09-17): `XiaomiMiMo/MiMo-V2.5` tokenizer.json downloaded from HF (vocab 151,643); its probe deltas are **identical to qwen3 row-for-row** (MiMo-V2 switched to the Qwen vocab); qwen3 mismatches this probe set ⇒ MiMo-V2/V2.5 excluded transitively. Same for `xiaomi/mimo-v2.5` on OpenRouter.
- "Meta Muse Spark" — **excluded at species level (measured on both gateways)**: `meta/muse-spark-1.3-contributor-free` measured via the **OpenCode Zen Responses API (/v1/responses)** as **llama4 vocab, 24/24 probes exact, MAE 0.00**; OpenRouter's `meta/muse-glimmer-30b` agrees (llama4 24/24, wrapper +56). Every discriminating probe separates (emoji: Muse 20 / union 28; zh_long 15/21; fr 12/16). Bonus Muse fingerprints: default `reasoning effort=high` (13 of 16 output tokens were reasoning_tokens), `parallel_tool_calls=true`.
- "DeepSeek V4.1-Flash" — **double-excluded, with the vision tower now measured** (open weights on HF make this a ground-truth anchor): tokenizer is deepseek vocab (V3 lineage, 129,280) 24/24 exact on itself, wrapper +30; the tower is self-built `deepseek_v41_vision` (config.json: 32 layers, hidden 1024, patch 14, 3×3 pixel-shuffle downsample, 2D-RoPE, min_pixels≈544², max 1024 tokens/image), and its flat **+184** for small images (64×64 = 256×256 = 184) is the min_pixels upscale floor in action — unlike union's floorless flat 24. Different vocab, different tower.
- "Circulating tokenizer-analysis image (2026-09-17, three-panel composite)" — **conditionally falsified (local replay, zero API calls)**: the left panel claims GLM-5/5.1/5.2/5.3 match the model 9/10 exact with +43 outliers, shown beside benchmark charts labeling "Union Alpha (anticipated pricing)★" — implying union = GLM. Replaying the archived API deltas (10 probes, majority path): **glm5 scores 2/10** (only ja, code_indent), llama3 9/10 (en_pangram is the archived unstable row) — the image's "9/10" is exactly llama3's true match count, and the +43 outlier has no corresponding constant in union data (wrapper stably 16/17). Either llama3-vocab data was relabeled onto the GLM-5 row, or the panel analyzes a different genuinely-GLM-vocab stealth model spliced next to union's benchmark points. The "anticipated pricing" labels are self-declared guesses. Under either reading, the implied union=GLM conclusion contradicts the archived species-level llama3 verdict.
- llama3 vocab + real vision + 256K context is consistent with a **Llama 3.x-based multimodal derivative** (context extended beyond native 128K) or a new training run reusing the llama3 vocab. No publicly known model matches all three of vocab, vision billing shape (flat 24 patches for small images), and 262K — supporting "undisclosed new training/derivative" as the conclusion.

### Inside the llama3 post-training family (vocab has no power; discriminate by template)

Public post-trainings (Hermes, Tülu, Llama-Nemotron) all sit on Llama 3.1/3.3 bases — same vocab as union-alpha, so the differential is blind inside this circle; discrimination moves to the template and behavior layers:

| Peer | Vocab | wrapper | Verdict |
|---|---|---|---|
| union-alpha | llama3 | **+16/17** | — |
| hermes-3-llama-3.1-405b | llama3 (24/24) | +10 (ChatML) | **not a Hermes-template model** |
| muse-glimmer-30b / muse-spark-1.3 | llama4 (24/24 ×2) | +56 / — | outside the vocab circle |
| nemotron-3-nano-30b | proprietary (no candidate matches, no-winner shape) | +16 | NVIDIA's 2026 line unrelated to union-alpha |
| meta-llama/llama-3.1-8b-instruct | usage polluted by prompt cache (deltas shifted +23~24, non-uniform) | anchor unavailable | OpenRouter's official-llama usage semantics unreliable (same cause as the llama-3.3-70b case) |

The surviving explanation (matching the community analysis): an **undisclosed internal derivative** of Llama 3.1-70B/3.3-70B/405B with a vision adapter and a 262K window extension — post-training shops have this pipeline ready-made, but no public catalog SKU fits.

---

## Methodology notes (new in this case)

1. **OpenCode free-tier access**: OpenCode Zen free models reject bare API calls with `MissingSessionID` ("free tier can only be used in OpenCode"); any UUID in an `x-session-id` header unlocks it. `ChatClient` now sends one by default.
2. **OpenRouter usage semantics are not comparable across models**: named `meta-llama/llama-3.3-70b-instruct` (via DeepInfra) returned prompt_tokens inconsistent with vocab theory, with negative deltas — same-gateway named A/B (L7) is unusable on OpenRouter; tokenizer verdicts should rest on **local tokenizer comparison** (zero API cost, zero semantic ambiguity).
3. **"context-compression plugin" is OpenRouter gateway copy**, not an upstream fingerprint; the 262,144 it quotes is OpenRouter-side config. Real limits need needle measurement.
4. **HTTP 200 wrapping errors**: OpenRouter returns HTTP 200 + `{"error":{"code":502,...}}` for upstream 5xx — error-envelope analysis must parse bodies, not status codes.
5. **Age attestation is a model-level routing gate**: all `meta/muse-spark-*` variants (contributor included) return 403 with `missing_attestation_types: ["age_18plus"]` until the OpenRouter account completes 18+ confirmation — treat attestation status as a prerequisite when designing peer-comparison experiments; the same model can be measured on another gateway (OpenCode's free tier has no such gate).
6. **The wrapper constant drifts**: same probes, same vocab, two sessions: +17 → +16. Template constants are good for family-level judgments, not cross-session exact identity claims.
7. **OpenCode's Muse line speaks the Responses API**: `/zen/v1/chat/completions` returns 500 for Muse models; the working endpoint is `/zen/v1/responses` (`usage.input_tokens`). The gateway also filters by User-Agent — `Python-urllib/*` gets 403, a `curl/*` UA passes — and the free tier allows ~1 concurrent request (4 parallel = 403).
8. **Inside the llama3 vocab circle, discriminate by template constant, not vocab**: hermes-3 (llama3 vocab 24/24) has wrapper +10 vs union's +16/17 — a usable discriminator within the same-vocab family. But official-llama models on OpenRouter have usage polluted by prompt caching (deltas shifted +23~24, non-uniform), so the official-template anchor is unobtainable on that gateway.
9. **`min_pixels` floor is a measurable vision-tower signature**: DeepSeek V4.1-Flash (open weights) bills a flat **+184** for any image below ~544×544 — exactly the config's `min_pixels=295,936` upscale floor ((544/14)²/9 ≈ 168 + overhead), confirmed against its published `vision_config`. Both "flat for small images" shapes below are real encoders, but a *high* flat value (V4.1-Flash: 184, floor-enforced) vs a *floorless* flat value (union: 24, even 1×1) separates towers that a "flat vs slope" test alone would lump together.
10. **OpenCode's free tier is a paid catalog with a tiny free sample** (full census 2026-09-17): of the 71 listed models, 62 return 401 CreditsError, and only 4 are actually free-reachable (5.6%): `muse-spark-1.2/1.3-contributor-free` (both llama4 12/12 — the family exclusion now covers v1.2 too), `ling-3.0-flash-fin-free` and `nemotron-3.5-lightning-free` (both proprietary-vocab no-winner shapes). union-alpha stays 500 (upstream outage ongoing since 09-16); `deepseek-v4-flash-free` answers 400 "Model is unavailable" (pulled after the V4.1-Flash release). The error envelopes name the upstream provider "Console".

## Toolkit usage

```bash
export STEALTHPRINT_BASE_URL="https://openrouter.ai/api/v1"
export STEALTHPRINT_MODEL="stealth/union-alpha"
export STEALTHPRINT_API_KEY="sk-or-..."   # OpenRouter key (needs credit balance; the model itself is $0)

stealthprint tokenizer --repeats 4   # L1 (mixed-aware: repeat -> mode -> pair within majority path)
stealthprint wrapper                 # L2 length ladder
stealthprint wrapper --turns 4       # L2+ multi-turn accumulation (negative delta = backend path switch)
stealthprint echo                    # L1b completion-side vocab check (echo probes)
stealthprint tools                   # L5 tool-calling fingerprint
stealthprint errors                  # L4
stealthprint vision                  # L6 color ground truth + token slope
stealthprint video                   # L6 video shapes
stealthprint context --skip-search --needle-size 200000   # L3 needle
```

Cost: tiny `max_tokens` throughout (upstream does not enforce it, completions run slightly over), model price 0, ~200 calls this session for under $0.01 total (including the muse-glimmer-30b peer run at ~$0.0004); all comparison tokenizers local.

## License

MIT
