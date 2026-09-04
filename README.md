# stealthprint

对任何 OpenAI 兼容端点上的 stealth / 匿名模型做指纹分析的可复用库。库本身**不绑定任何模型名**——端点、模型 ID、密钥全部显式传入，换一个新模型改两个参数即可复跑全流程。

[English](#english) · [中文](#中文)

## English

### Install

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"
# or with fingerprint-layer dependencies:
pip install -e "git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"
```

### CLI

```bash
export STEALTHPRINT_BASE_URL="https://api.example.com/v1"
export STEALTHPRINT_MODEL="mystery-model"
export STEALTHPRINT_API_KEY="sk-..."
export STEALTHPRINT_LANG=zh          # en (default) | zh

stealthprint tokenizer --tokenizers tok/   # L1: vocab differential (run this first, cheapest + hardest evidence)
stealthprint wrapper                       # L2: chat-template overhead constant
stealthprint context                       # L3: context limit + needle retrieval
stealthprint errors                        # L4: error envelope family (serving-stack language)
stealthprint vision                        # L6: vision ground truth (colors + token slope)
```

Everything can also be passed explicitly per command: `stealthprint --base-url ... --model ... --api-key ... tokenizer`.

Add `--json` for machine-readable output alongside the human summary.

### Python API

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

### Custom probe sets

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
emoji ZWJ/flags, rare Unicode, digits/floats, punctuation. Differential
`Δ = T(base+probe) − T(base)` cancels the chat-template constant, leaving only
the model's own tokenizer count.

### Languages / 多语言

Output messages and CLI help support `en` and `zh`. Add a language by appending
a catalog in `stealthprint/i18n.py`:

```python
CATALOGS["ja"] = {"cli.tok": "L1 トークナイザ差分 ...", ...}
```

Data keys stay English regardless of display language.

### Layer reference

| Layer | Command | Answers |
|---|---|---|
| L1 tokenizer | `tokenizer` | Which open vocab does this model tokenize like? Hardest evidence; all comparison tokens are local files, zero extra API cost |
| L2 wrapper | `wrapper` | How much fixed chat-template overhead does the gateway add? Same model via different entries may differ; vocab does not |
| L3 context | `context` | Verified context ceiling (binary search) + long-context retrieval fidelity (head/mid/tail needles) |
| L4 errors | `errors` | Serving-stack fingerprints: numeric vs string codes, serde/Java error text, accepted roles, validation style |
| L6 vision | `vision` | Is vision real? Color ground truth + token-overhead slope across image sizes (constant => placeholder; size-scaled => real encoder) |

Self-identification ("who are you"), prompt injection, censorship probes and
emoji density are deliberately **not** implemented as layers — they are bait
prone for stealth models. See the case study for why.

### Fetching comparison tokenizers

```bash
./fetch_tokenizers.sh            # downloads open tokenizer.json candidates into tok/
```

Candidates are just files in a directory — drop in any `tokenizer.json` to add
a suspect (e.g. `tok/mynewmodel.json`). tiktoken's `o200k_base` is always
included automatically.

## 中文

对任何 OpenAI 兼容端点上的匿名模型做指纹分析的可复用库。不绑定任何模型：`--base-url` / `--model` / `--api-key` 全部显式传入，换新模型零代码改动。

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"

export STEALTHPRINT_BASE_URL="https://api.example.com/v1"
export STEALTHPRINT_MODEL="mystery-model"
export STEALTHPRINT_API_KEY="sk-..."

stealthprint tokenizer --tokenizers tok/   # 先跑：最便宜、最硬的分词器差分
stealthprint context                        # 上下文上限 + 针找回
stealthprint errors                         # 错误信封家族（服务栈语言）
stealthprint vision                         # 视觉真值测试
stealthprint --lang zh tokenizer            # 中文输出
```

设计要点：

- **分层证据**：L1 分词器差分（定种级）→ L2 wrapper 常数（栈级）→ L3 上下文/针（行为级）→ L4 错误信封（栈级）→ L6 视觉真值（行为级）。
- **对照零成本**：候选词表全部是本地开源 `tokenizer.json`，只需要目标模型一端的 API。
- **数据键恒为英文**：`ranking` / `wrapper` / `api_delta` 等适合脚本化对比；界面文案走 i18n（`en` / `zh`，在 `i18n.py` 里加目录即可扩展）。
- **刻意不实现**：自称探针、注入、审查、emoji 密度——stealth 模型的自我介绍经常是诱饵。

完整案例（GLM-5 词表判定、~1M 上下文、Rust 网关、异构视觉后端）见
[docs/case-omen-alpha.md](docs/case-omen-alpha.md)。

## Prior art

Methodology builds on `iSimplifyMe/tokenizer-fingerprint`,
`LuD1161/ox-alpha-identification-public`, and `unclecode/modelprint`.
stealthprint adds: heterogeneous-backend detection, vision ground-truth
protocol, wrapper-constant verification, and multilingual probe sets.

## License

MIT
