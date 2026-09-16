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
| Mixing | Same-shape requests split across prompt_tokens paths (base text: 33 × 17, 15 × 6, 21 × 1 over 24 calls); multi-turn deltas go **negative** (turn 3→4: 46→40 — impossible on monotonic content = path switch, so the turns probe doubles as a mixing detector); both paths report `provider: "Stealth"` | High (phenomenon) / Low (secondary-path attribution) |

### Mixed backends

The secondary (low) path's counts are **unstable across rounds** (fr probe 19→29, ru 34→30), match no local candidate vocab, and are not "majority + constant" — ruling out a single model with a different template and pointing at a **different backend/counter**. Differential probes must pair within one path (majority mode; now codified in the library as `tokenizer --repeats`).

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
- "Meta Muse Spark" — **likely excluded** (medium-high confidence): `meta/muse-glimmer-30b` (the Muse family small model) measured **llama4 vocab, 24/24 probes exact, MAE 0.00**, wrapper +56 — every discriminating probe differs from union-alpha's llama3 match. Family-vocab consistency is an inference, not a Spark-1.3 measurement — `meta/muse-spark-1.3` is gated behind OpenRouter's 18+ age attestation; retest after the account owner confirms.
- llama3 vocab + real vision + 256K context is consistent with a **Llama 3.x-based multimodal derivative** (context extended beyond native 128K) or a new training run reusing the llama3 vocab. No publicly known model matches all three of vocab, vision billing shape (flat 24 patches for small images), and 262K — supporting "undisclosed new training/derivative" as the conclusion.

---

## Methodology notes (new in this case)

1. **OpenCode free-tier access**: OpenCode Zen free models reject bare API calls with `MissingSessionID` ("free tier can only be used in OpenCode"); any UUID in an `x-session-id` header unlocks it. `ChatClient` now sends one by default.
2. **OpenRouter usage semantics are not comparable across models**: named `meta-llama/llama-3.3-70b-instruct` (via DeepInfra) returned prompt_tokens inconsistent with vocab theory, with negative deltas — same-gateway named A/B (L7) is unusable on OpenRouter; tokenizer verdicts should rest on **local tokenizer comparison** (zero API cost, zero semantic ambiguity).
3. **"context-compression plugin" is OpenRouter gateway copy**, not an upstream fingerprint; the 262,144 it quotes is OpenRouter-side config. Real limits need needle measurement.
4. **HTTP 200 wrapping errors**: OpenRouter returns HTTP 200 + `{"error":{"code":502,...}}` for upstream 5xx — error-envelope analysis must parse bodies, not status codes.
5. **Age attestation is a model-level routing gate**: all `meta/muse-spark-*` variants (contributor included) return 403 with `missing_attestation_types: ["age_18plus"]` until the OpenRouter account completes 18+ confirmation — treat attestation status as a prerequisite when designing peer-comparison experiments.
6. **The wrapper constant drifts**: same probes, same vocab, two sessions: +17 → +16. Template constants are good for family-level judgments, not cross-session exact identity claims.

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
