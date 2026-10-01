# stealthprint

[English](README.md) · [中文](README.zh-CN.md)

A reusable library for fingerprinting stealth / anonymous models served behind
any OpenAI-compatible endpoint. The library is **bound to no model name** —
endpoint, model id, and API key are always passed explicitly; switching to a
new model means changing two parameters and rerunning the same playbook.

[First investigation](#first-investigation-compare-tokenizers-before-larger-probes) · [Layer reference](#layer-reference) · [Case studies](#case-studies)

## Install

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"
# or with fingerprint-layer dependencies:
pip install -e "git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"
```

## CLI

```bash
export STEALTHPRINT_BASE_URL="https://api.example.com/v1"
export STEALTHPRINT_MODEL="mystery-model"
export STEALTHPRINT_API_KEY="sk-..."
export STEALTHPRINT_LANG=zh          # en (default) | zh

stealthprint tokenizer --tokenizers tok/   # L1: vocab differential (run first: cheapest + hardest evidence)
stealthprint wrapper                       # L2: chat-template overhead constant
stealthprint context                       # L3: context limit + needle retrieval
stealthprint errors                        # L4: error envelope family (serving-stack language)
stealthprint vision                        # L6: vision ground truth (colors + token slope)
stealthprint vision --repeats 24           # L6: repeated identical 64x64 probe (backend mix)
stealthprint video                         # L6: video_url / type:video content-block shapes
stealthprint catalog --family glm          # L7: same-gateway A/B vs named catalog siblings
stealthprint survey --out census.jsonl     # census the whole catalog: classify reachability
                                           # (paid-walled / rate-limited / upstream errors), then
                                           # L1-probe the reachable models (--max-models budget guard,
                                           # resume-safe via the jsonl, Muse-style /responses fallback)
```

Every identity parameter can also be passed per command:
`stealthprint --base-url ... --model ... --api-key ... tokenizer`.
Global flags work before or after the subcommand. Add `--json` for
machine-readable output alongside the human summary.

## First investigation: compare tokenizers before larger probes

1. Install the fingerprint dependencies (`stealthprint[all]`) and fetch the
   [candidate tokenizer files](#fetching-comparison-tokenizers). Set the endpoint,
   model and API-key environment variables shown above.
2. Inspect `stealthprint tokenizer --help`, then run
   `stealthprint tokenizer --tokenizers tok/ --json`. Target API calls may be
   billable; comparing the returned counts with local candidates adds no model
   requests. Repeats and additional layers create more requests.
3. Read each candidate's `exact`, `total` and `mae`. An exact match means matching
   token-count deltas on the successful probes, not proof of the model's weights,
   architecture, training data or vendor. Failed probes reduce `total`; compare
   the same successful set before interpreting a ranking.
4. Check a second signal with `stealthprint wrapper`, or use
   `stealthprint tokenizer --tokenizers tok/ --repeats 3` if repeated counts vary.
   Preserve endpoint, model ID, time, probe set and failures with the result.
5. Choose further layers for a specific unanswered question. The
   [layer reference](#layer-reference) and [worked cases](#case-studies) show how
   tokenizer, wrapper, context and modality evidence fit together.

### How to interpret an inconclusive result

- **No candidate matches?** Your tokenizer directory may omit the right vocab;
  a gateway can also alter counting or route requests between backends. Add a
  relevant published candidate and inspect repeat behavior before attributing
  identity.
- **Missing dependencies?** The tokenizer layer requires both `tokenizers` and
  `tiktoken`; use the fingerprint dependency installation above.
- **Does a failed large prompt prove a context limit?** No. The context search
  treats request failures as unsuccessful sizes. Rate limits, timeouts and HTTP
  body-size limits can affect it. Report verified successful input sizes and
  failures, rather than inferring a model's architectural maximum.
- **Is survey free or capped in dollars?** `--max-models` limits model count, not
  API spend. For a small, resumable catalog sample use
  `stealthprint survey --max-models 2 --out census.jsonl`; inspect provider pricing
  before running it. Existing JSONL entries are skipped when resuming.

## Python API

```python
from stealthprint import ChatClient, tokenizer_differential

client = ChatClient(
    model="mystery-model",
    base_url="https://api.example.com/v1",
    api_key="sk-...",
)
result = tokenizer_differential(client, tokenizers_dir="tok")
print(result["ranking"])     # per-candidate exact/mae
print(result["wrapper"])     # gateway template constant per candidate
```

Each layer returns a plain dict with stable English keys — safe for scripts and
long-term comparison. Human-readable output is localized.

## Custom probe sets

```bash
stealthprint tokenizer --probes my_probes.json
```

```json
{
  "base": "You are a helpful assistant. Repeat the following text exactly and add nothing else:\n\n",
  "probes": [["my_probe", "any text that separates candidate tokenizers"]]
}
```

The bundled set (`stealthprint/data/probes.json`) covers 12+ languages: English
pangram, Chinese, Japanese, Korean, Thai, Russian, French, code/indentation,
emoji ZWJ/flags, rare Unicode, digits/floats, punctuation. The differential
`Δ = T(base+probe) − T(base)` cancels the chat-template constant, leaving only
the model's own tokenizer count.

## Localization

CLI help and output messages support `en` and `zh` (`--lang` or
`STEALTHPRINT_LANG`). Add a language by appending a catalog in
`stealthprint/i18n.py`:

```python
CATALOGS["ja"] = {"cli.tok": "L1 トークナイザ差分 ...", ...}
```

Data keys stay English regardless of display language.

## Layer reference

| Layer | Command | Answers |
|---|---|---|
| L1 tokenizer | `tokenizer` | Which open vocab does this model tokenize like? Hardest evidence; all comparison tokens are local files, zero extra API cost |
| L2 wrapper | `wrapper` | How much fixed chat-template overhead does the gateway add? Same model via different entries may differ; vocab does not |
| L3 context | `context` | Verified context ceiling (binary search) + long-context retrieval fidelity (head/mid/tail needles) |
| L4 errors | `errors` | Serving-stack fingerprints: numeric vs string codes, serde/Java error text, accepted roles, validation style |
| L6 vision | `vision` | Is vision real? Color ground truth + token-overhead slope across image sizes (constant => placeholder; size-scaled => real encoder). `--repeats N` for mix rate |
| L6 video | `video` | Does the model accept native video (`video_url`) vs unknown `type: video` vs mp4-as-image? |
| L7 catalog | `catalog` | Same-gateway A/B: wrapper offset, special-token delta, `reasoning_effort=none`, one image vs named siblings (`--peers` / `--family`) |

Self-identification ("who are you"), prompt injection, censorship probes and
emoji density are deliberately **not** implemented as layers — for stealth
models, self-descriptions are frequently bait. See the case study for why.

## Fetching comparison tokenizers

```bash
./fetch_tokenizers.sh            # downloads open tokenizer.json candidates into tok/
```

Candidates are just files in a directory — drop in any `tokenizer.json` to add
a suspect (e.g. `tok/mynewmodel.json`). The fetch script includes Mistral-Nemo's
published `tokenizer.json`, checked against its Tekken file on bundled probes;
tiktoken's `o200k_base` and `cl100k_base`
are included automatically. Vendors that publish only `tiktoken.model` (e.g.
Moonshot Kimi) can be built with tiktoken directly — see the reproduce
section of the space-bunny case.

## Case studies

Worked cases and an unresolved investigation, with measurement details:

- **space-bunny-free → MiniMax** (2026-09-24): 24/24 vocab (Kimi 13/24, GLM
  7/24), same-gateway delta identity with `minimax-m3`/`m2.5`, ctx ≥1M rules
  out the m2.5 generation, +143 wrapper zero-drift, adapter-class vision,
  homogeneous pool, improvised ChatGPT self-report.
  [docs/case-space-bunny.md](docs/case-space-bunny.md) ·
  [中文](docs/case-space-bunny.zh-CN.md)
- **omen-alpha → GLM-5.3-Flash** (2026-09-04): GLM-5 vocab 24/24, ~1M
  context, Flash-class image+video, +24 stealth wrapper vs named
  `glm-5.3-flash`, Rust serving stack.
  [docs/case-omen-alpha.md](docs/case-omen-alpha.md) ·
  [中文](docs/case-omen-alpha.zh-CN.md)
- **union-alpha → Unbiased Pareto** (2026-09-16): OpenRouter free preview
  unmasked at EOL by its own 404; successor fingerprint-continuous
  (B-channel delta pairs exact), billing counter masquerading as an
  unproducible mystery counter.
  [docs/case-union-alpha.md](docs/case-union-alpha.md) ·
  [中文](docs/case-union-alpha.zh-CN.md)

- **pixel-canary — identity unresolved** (2026-09-26): candidate-tokenizer and named-model comparisons did not establish identity. See the [Chinese investigation and measurement links](docs/case-pixel-canary.zh-CN.md) for limits and unsuccessful comparisons.

## Prior art

Methodology builds on `iSimplifyMe/tokenizer-fingerprint`,
`LuD1161/ox-alpha-identification-public`, and `unclecode/modelprint`.
stealthprint adds: heterogeneous-backend detection, vision ground-truth
protocol, wrapper-constant verification, video modality probes, same-gateway
catalog A/B, and multilingual probe sets.

## License

MIT
