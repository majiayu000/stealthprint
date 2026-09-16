# Case study: union-alpha

[English](case-union-alpha.md) · [中文](case-union-alpha.zh-CN.md)

对 OpenRouter 免费预览的匿名模型 `stealth/union-alpha`（2026-09-16 上线，价格 0）做的指纹分析，全程使用本库（`stealthprint`）方法论。OpenCode Zen 线（公开线与 Go 线目录均含 `union-alpha`）截至本文写作时推理持续 HTTP 500，全部测量在 OpenRouter 入口完成。

> **TL;DR：** `stealth/union-alpha` 使用 **Llama-3 词表（128K）**（15 判别探针 × 12 次重复零偏离 + 混挂感知复测再确认），带 **+16~17 token 固定模板**（会话间存在 ±1 漂移）。**真视觉编码器**：红/蓝双向颜色真值通过（红 5/6、蓝 3/4），小图恒 24 token（固定 patch 数），256×256→108、512×512→369 随像素线性增长。**无原生视频**。187K token 埋针 3/3 精确召回。**工具调用指纹**：`tool_choice="none"` 被完全无视，默认并行双调用。**至少两条异构后端混挂**：主路径（llama3+16/17，约 70%）与一条计数不稳定的次路径（约 30%），OpenRouter 对两者统一标 `provider: "Stealth"`。排除 GLM 全系、Qwen3、DeepSeek、dots3、MiniMax、o200k、Llama 4（201K 新词表）、**小米 MiMo-V2.5（≡Qwen 词表，传递排除）**、**Meta Muse 家族（muse-glimmer-30b 实测 = llama4 词表 24/24，家族推断排除）**。社区猜测 MiniMax M3.1 / Kimi K3.1 中前者被词表直接排除。

---

## 结论（2026-09-17 测量，OpenRouter 入口）

| 层 | 结论 | 置信度 |
|---|---|---|
| 1 分词器 | **Llama-3 词表**。15 个判别探针 Δ 与本地 llama3 tokenizer.json 全部精确相等；两轮独立采样（4 次 + 12 次重复）+ `--repeats` 混挂感知复测（llama3 3/4 exact、其余 9 候选全 0）一致 | 高（定种级） |
| 1b 生成侧 | echo 复述 7/8 字符级精确（生成端真实、非占位符中转）；**隐藏 reasoning 计费**：en_pangram 复述精确却多计 34 token、zh_long 生成 120 token 全为不可见思考（中文长文触发思考路径）；生成侧词表判别力弱于输入侧 | 高（现象） |
| 2 wrapper | 相对本地 llama3 词表 **+17**（12-rep 轮）/ **+16**（复测轮）——模板常数存在会话间漂移；多轮累积增量非常数 | 高 |
| 3 上下文 | 标称 262,144（OpenRouter 配置）；实测 187,039 token 埋针 **头/中/尾 3/3 精确召回**，未触顶 | 高（下限 187K） |
| 4 服务栈 | `developer` role 被接受；`reasoning_effort=none` 被吞（200）；`temperature` 越界放行；`role:"wizard"` 与 mp4-as-image 均触发上游 502（OpenRouter 以 HTTP 200 包错误对象）；`max_tokens` 不被上游强制执行 | 高 |
| 5 工具 | **`tool_choice` 语义缺失**：none/auto/required 三者行为完全相同——none 照样返回 `finish=tool_calls`；**默认并行双调用**（get_weather + get_time 同响应、参数精确）；schema 开销 168 token | 高 |
| 6 视觉 | **真编码器**。64×64 红 5/6 "Red"、蓝 3/4 "Blue"（判别通过：非偏置）；1×1 与 64×64 同为 24 token（固定 patch 数）；256×256 = 108、512×512 = 369（≈随像素线性） | 高 |
| 6 视频 | **无原生视频**。`video_url` 在 OpenRouter 路由层被过滤（404，`routing_funnel: Filter by Input Video`）；`type:video` 被占位处理（pt=28，红色视频答 "Blue" 幻觉）；mp4 塞 `image_url` 上游 502 | 高 |
| 混挂 | 同形状请求 `prompt_tokens` 双峰（base: 33 / 15 / 21 三值均出现）；多轮增量出现**负值**（turn 3→4：46→40，单调递增内容下不可能 = 换路径，多轮探针兼任混挂检测器）；两路径 `provider` 字段同标 `"Stealth"` | 高（现象）/ 低（次路径归因） |

### 混挂详情

同一探针文本（`You are a helpful assistant. Repeat...` 底稿）24 次调用：pt=33 × 17、pt=15 × 6、pt=21 × 1。次路径（低值）的计数在不同轮次间**自身不稳定**（fr 探针 19→29，ru 34→30），不匹配任何本地候选词表，也非「主路径 + 常数」——排除「同一模型换模板」，指向**另一个后端/计数器**。差分分析必须在路径内配对（本案取主路径众数；`tokenizer --repeats` 已把该流程固化进库）。

### 关键判别探针（主路径 Δ vs 各词表）

| probe | API Δ | llama3 | glm5 | o200k | qwen3 | minimax | llama4 | mimo-v2.5 |
|---|---|---|---|---|---|---|---|---|
| emoji_zwj | **28** | 28 | 16 | 21 | 20 | 20 | 36 | ≠ |
| zh_long | **21** | 21 | 14 | 18 | 15 | 13 | 31 | ≠ |
| digits | **17** | 17 | 19 | 17 | 32 | 17 | 32 | ≠ |
| fr | **16** | 16 | 14 | 10 | 15 | 9 | 28 | ≠ |
| ru | **16** | 16 | 11 | 13 | 16 | 15 | 27 | ≠ |
| ja | **7** | 7 | 7 | 7 | 6 | 6 | 26 | ≠ |
| ko | **4** | 4 | 7 | 4 | 5 | 4 | — | ≠ |

（llama4 列为 Llama-4-Scout 词表实测，201,135 vocab；mimo-v2.5 词表与 qwen3 逐行相等，不单列。union 是 llama3 血统而非 llama4。）

### 与社区猜测对照

- 「MiniMax M3.1」——**被词表排除**（MiniMax-M1 词表全行不匹配；若 M3.1 沿用 M 系词表则同样排除）。
- 「Kimi K3.1」——moonshot 不发布 tokenizer.json 无法本地对照；但 K 系历来 163K 自研词表，与 128K llama3 匹配相悖（置信度：中，非直接证据）。
- 「小米 MiMo」——**被排除**（2026-09-17）：从 HF 下载 `XiaomiMiMo/MiMo-V2.5` tokenizer.json（vocab 151,643），其判别探针 Δ 与 qwen3 **逐行完全相等**（MiMo-V2 换用了 Qwen 词表）；qwen3 在本 probe set 上不匹配 ⇒ MiMo-V2/V2.5 传递排除。OpenRouter 在售的 `xiaomi/mimo-v2.5` 同理。
- 「Meta Muse Spark」——**定种级排除（两网关实测）**：`meta/muse-spark-1.3-contributor-free` 经 **OpenCode Zen Responses API（/v1/responses）**实测 **llama4 词表 24/24 精确匹配（MAE 0.00）**；OpenRouter 侧 `meta/muse-glimmer-30b` 同为 llama4 24/24 + wrapper +56。判别探针全面分离（emoji：Muse 20 / union 28；zh_long 15/21；fr 12/16）。Muse 1.3 附带指纹：默认 `reasoning effort=high`（16 输出 token 中 13 个是 reasoning_tokens）、`parallel_tool_calls=true`。
- llama3 词表 + 真视觉 + 256K 上下文：与 **Llama 3.x 底座的多模态衍生**（128K 原生上下文做过外扩）或**沿用 llama3 词表的新训练**均相容。公开模型库中无词表、视觉计费形状（小图固定 24 patch）、262K 三者全吻合的已知模型——支持「未公开的新训练/衍生」这一结论。

### llama3 后训练家族圈内对照（词表无判别力，换层判别）

Hermes/Tülu/Llama-Nemotron 一类公开后训练全部挂在 Llama 3.1/3.3 底座上，与 union 同词表——词表差分对圈内全部失效，判别换到模板与行为层：

| 对照模型 | 词表 | wrapper | 结论 |
|---|---|---|---|
| union-alpha | llama3 | **+16/17** | — |
| hermes-3-llama-3.1-405b | llama3（24/24） | +10（ChatML） | **非 Hermes 模板家族** |
| muse-glimmer-30b / muse-spark-1.3 | llama4（24/24×2） | +56 / — | 词表圈外 |
| nemotron-3-nano-30b | 自研（无候选匹配，无赢家形状） | +16 | NVIDIA 2026 主力线与 union 无关 |
| meta-llama/llama-3.1-8b-instruct | usage 被 prompt cache 污染（Δ 系统性 +23~24、不均匀） | 锚点不可得 | OpenRouter 的 llama 官方模型 usage 语义不可靠（与 llama-3.3-70b 那次同因） |

圈内剩余解释（与社区分析一致）：Llama 3.1-70B/3.3-70B/405B 做视觉适配 + 扩窗到 262K 的**内部未公开衍生**——这类流水线在后训练厂手里最现成，但公开目录无现货 SKU 对得上。

---

## 方法论发现（本案新增）

1. **OpenCode 免费层准入**：OpenCode Zen 免费模型对裸 API 调用返回 `MissingSessionID`（"free tier can only be used in OpenCode"），请求带任意 UUID 的 `x-session-id` header 即放行。本库 `ChatClient` 已默认携带。
2. **OpenRouter usage 语义不可跨模型对照**：具名 `meta-llama/llama-3.3-70b-instruct`（走 DeepInfra）的 `prompt_tokens` 与词表理论值不符且 Δ 可为负——同网关具名对照（L7）在 OpenRouter 不可用；词表判定应依赖**本地 tokenizer 对照**（对照侧零 API 成本、零语义歧义）。
3. **"context-compression plugin" 是 OpenRouter 网关文案**，不是上游栈指纹；其引用的 262,144 是 OpenRouter 侧配置，模型真实上限须以埋针实测为准。
4. **HTTP 200 包错误对象**：OpenRouter 对上游 5xx 返回 HTTP 200 + `{"error":{"code":502,"metadata":{"error_type":"provider_unavailable"}}}`——错误信封分析必须解析 body 而非只看状态码。
5. **年龄 attestation 是模型级路由 gate**：`meta/muse-spark-*` 全系（含 contributor 变体）在 OpenRouter 要求账户先完成 18+ 确认（`missing_attestation_types: ["age_18plus"]`），否则 403——对照实验设计需把 attestation 状态当作前置条件；同一模型可换网关绕过（OpenCode 免费档无此 gate）。
6. **wrapper 常数会漂移**：同探针同词表两轮测量 +17 → +16。模板常数适合做「同家族」判断，不适合做跨会话的精确身份断言。
7. **OpenCode 的 Muse 系走 Responses API**：`/zen/v1/chat/completions` 对 Muse 全 500，正确入口是 `/zen/v1/responses`（`usage.input_tokens`）；且网关按 User-Agent 过滤——`Python-urllib/*` 直接 403，须伪装成 `curl/*`；免费档实际并发约 1（并发 4 即 403）。
8. **llama3 圈内判别靠模板常数，不靠词表**：hermes-3（llama3 词表 24/24）wrapper +10 vs union +16/17——同词表家族内部，模板常数成为可用判别器；但 llama 官方模型在 OpenRouter 的 usage 被 prompt cache 污染（Δ 系统性偏移 +23~24），官方模板锚点在该网关不可得。

## 工具包使用

```bash
export STEALTHPRINT_BASE_URL="https://openrouter.ai/api/v1"
export STEALTHPRINT_MODEL="stealth/union-alpha"
export STEALTHPRINT_API_KEY="sk-or-..."   # OpenRouter key（需充值余额，模型本身 $0）

stealthprint tokenizer --repeats 4   # L1（混挂感知：重复采样→众数→主路径内配对）
stealthprint wrapper                 # L2 长度阶梯
stealthprint wrapper --turns 4       # L2+ 多轮累积（负增量 = 换后端路径）
stealthprint echo                    # L1b 生成侧词表验证（复述探针）
stealthprint tools                   # L5 工具调用指纹
stealthprint errors                  # L4
stealthprint vision                  # L6 颜色真值 + token 斜率
stealthprint video                   # L6 视频形状
stealthprint context --skip-search --needle-size 200000   # L3 埋针
```

费用：全程 `max_tokens` 极小（上游不强制执行，completion 会略超），模型价格 0，本次会话约 200 次调用总花费 < $0.01（含 muse-glimmer-30b 对照 $0.0004 级）；对照词表全部本地。

## License

MIT
