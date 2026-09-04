# stealthprint

[English](README.md) · [中文](README.zh-CN.md)

A reusable library for fingerprinting stealth / anonymous models served behind
any OpenAI-compatible endpoint. The library is **bound to no model name** —
endpoint, model id, and API key are always passed explicitly; switching to a
new model means changing two parameters and rerunning the same playbook.

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
```

Every identity parameter can also be passed per command:
`stealthprint --base-url ... --model ... --api-key ... tokenizer`.
Global flags work before or after the subcommand. Add `--json` for
machine-readable output alongside the human summary.

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
a suspect (e.g. `tok/mynewmodel.json`). tiktoken's `o200k_base` is always
included automatically.

## Case study

Full worked example (GLM-5 vocab identification, ~1M context, Flash-class
image+video, +24 stealth wrapper vs named `glm-5.3-flash`):
[docs/case-omen-alpha.md](docs/case-omen-alpha.md) ·
[中文](docs/case-omen-alpha.zh-CN.md)

## Prior art

Methodology builds on `iSimplifyMe/tokenizer-fingerprint`,
`LuD1161/ox-alpha-identification-public`, and `unclecode/modelprint`.
stealthprint adds: heterogeneous-backend detection, vision ground-truth
protocol, wrapper-constant verification, video modality probes, same-gateway
catalog A/B, and multilingual probe sets.

## License

MIT
