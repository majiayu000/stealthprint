# crouton-alpha：usage 计数不可用，身份未定（2026-11-04）

**决定：很可能是 Meta 这条线，未确认。** Crouton 的真实切分和 Llama 4 词表一致（盲测 92/92，49 份词表中唯一通过）；同网关的具名 Meta 模型 Muse Spark 1.2、1.3 也是这份词表（L1 24/24，第二名 13/24）。GPT-6、Claude Haiku、Gemini、Grok 对照都不符。词表开源，其他团队也能用，权重和厂商没有被证明。它和 Pulse Alpha 不是同一个部署。

要支撑的决定：能否把 Crouton 定位到具体厂商。放弃条件：真实词表计数拿不到，且错误和行为层没有唯一指向时停止，改等官方揭晓或具名模型对照。第一层计数确实拿不到，但截断边界绕过了它。

测量数据：[case-crouton-alpha-measurements.json](case-crouton-alpha-measurements.json)。

## 词表计数失效

- 单个字母计数相差 0–4（`a` 19939、`x` 19935），重复请求结果不变。
- 不可叠加：两段文本各计 14，拼接后计 23。
- 非单调：`a` 比 `aaaa` 多 2；`123` 比 `123456789` 多 2。
- 长度规律近似估算：ASCII 约每 4 字符 1 个，CJK 约每字 1 个（`a`×1024 增量正好 256，`中`×100 增量 97）。
- 输出计数同样对不上内容：数字序列 390 字符计 113，o200k 为 298。
- `reasoning_tokens` 始终为 0，OpenRouter 却标注推理强制开启。
- 图片不计入 usage：纯色图 6/6 识别正确，`prompt_tokens` 没有增加。

同一中继下，deepseek、glm、pulse 的 8 个字母都同价，拼接也基本可叠加；deepseek 对本地词表 24/24。所以异常来自 Crouton 这条线，不是中继。

推断：usage 是估算值，可能叠加了按内容确定的扰动。是否有意遮蔽、发生在网关还是上游，都没有证据。

**因此 L1、视觉计数和 `stealthprint match` 的 `different` 判定，在这里都不能排除任何厂商。** `match` 给 hy3 的 `partial` 也是假信号：两边的图片计数都是 0。这个档案故意没有放进 `fingerprints/`。

## 截断边界：绕过 usage 的词表测试

被 `max_tokens` 截断的回复一定停在 token 边界上，不管隐藏思考占了多少预算；断点前的 token 数也不会超过 `max_tokens`。所以让模型复述固定文本，改变 `max_tokens`，就能不看 usage 判断词表。Crouton 用 `reasoning_effort=minimal`（不支持 `none`，其他档位常把预算耗光）。

通过标准：所有断点都是该词表的边界，且没有一次超预算。

| 文本 | 模型 | 断点数 | 49 份词表中通过的 |
|---|---|---|---|
| t1（探索） | crouton | 68 | `llama4` |
| t2（探索） | crouton | 104 | `llama4` |
| **t3（盲测）** | **crouton** | **92** | **`llama4`** |
| t1 | deepseek-v4.1-flash-free | 32 | `deepseek`、`step3` |
| t1 | glm-5.3-flash-free | 24 | `glm53` 等 9 份，不含 `llama4` |
| t1 | mimo-v2.6-flash-free | 15 | `mimo-v2.5` 等 12 份，不含 `llama4` |
| t3 | gpt-6-luna | 34 | `o200k_base` |
| t3 | claude-haiku-5-5 | 55 | 无（`llama4` 38/55） |
| t3 | gemini-4-argon | 57 | 无（`llama4` 54/57，超预算 32 次） |
| t3 | grok-4.7（只跑了 28/61） | 19 | 无（`llama4` 超预算 19 次） |

过程要如实记录：
- t2 上预先写下的预测**没过**（100/104）。4 个没命中的点都是半字节 token，确认是评分器错误后才修，所以 t1、t2 的结果是事后得到的。
- t3 是修复后的正式盲测：文本、算法和判定标准都先锁定、校验过哈希才发请求。
- t3 有 15 条回复被排除，都是模型在 `。` 后多打一个空格，和词表无关。

数据和评分脚本：[case-crouton-truncation.json](case-crouton-truncation.json)、`scripts/truncation_score.py`。

复现（2026-11-05）：
- 词表按 `tokenizers.lock` 从零下载后，用存档数据重算，结果不变；候选增加 `kimi-k2` 后共 50 份。`llama4` 取自 `unsloth/Llama-4-Scout-17B-16E-Instruct`，因为 `meta-llama` 仓库需要授权。
- 用 `stealthprint profile` 和不含本地规则文件的信封全新测一次：52 个断点，仍然只有 `llama4` 通过，但领先第二名只有 1 处。单次运行的区分力有限，要看三段文本合计的结果。
- 自身一致性：GPT-6、DeepSeek、Grok 自己的 124 个断点都落在自己的流式边界上。
- 断点只能否定词表，不能肯定：GPT-6 的 33 个断点也全落在 DeepSeek 的流式边界上。
- `max_tokens` 在 Grok 上不严格：32 个断点，`llama4` 等词表每个都超预算，所以 Grok 的结果只能作否定证据。

局限：
- 只比了 49 份本地词表，没有的词表无法排除，比如 Meta 更新的闭源模型。
- 词表可以被其他团队复用，所以这不证明权重或厂商。
- 没有具名 Meta 模型做同线对照。
- DeepSeek、GLM、MiMo 的对照只用了 t1，且部分请求被限流；t3 上它们 41 次全部被限流。

## 和 Muse Spark 对照

| 项目 | Crouton | Muse Spark 1.2 / 1.3 |
|---|---|---|
| 词表证据 | 截断边界只通过 `llama4` | L1 `llama4` 24/24（两版差值完全相同） |
| `stop` 报错 | `Responses does not support stop sequences` | 相同（这句来自网关适配层） |
| `max_output_tokens` 下限 | 16 | 16，但措辞不同（`The number must be >= 16`） |
| `reasoning_effort=none` 报错 | `reasoning_effort 'none' is not supported for model '<id>'. Supported values: [minimal, low, medium, high, xhigh, max]` | 1.3 逐字相同（只有模型名不同）；1.2 是另一句 |
| 流式 | 成批 | 成批（1.3 全文 43 块） |
| usage 计数 | **估算，自检不通过** | **真实，自检通过** |
| `reasoning_tokens` | 始终 0 | 真实上报 |

相同点里有的来自网关，不能全算作模型证据：`stop` 那句肯定是，`none` 报错带 `Upstream request failed`，来自上游，但没和其他厂商逐一比对过（GPT-6 的措辞不同）。usage 是明确的不同：Crouton 的计数被估算或遮蔽，Muse 没有。

Muse 的截断测试区分力不够：思考吃掉大部分预算，1.3 只留下 14 个断点（`llama4` 和 Gemma 系都过），1.2 一个都没有。所以两者的词表证据来自两种不同方法，没有在同一方法下直接比对。

Zen 线的 `muse-spark-1.3`、`muse-wonder-1.0` 没测。

## 还可用的线索

- 走 Responses 适配：`stop: Responses does not support stop sequences`，报错路径是 `input[1].content[1]`。对照的三个模型都接受 `stop`。
- `max_output_tokens` 下限 16。GPT 线也有这个字段和下限，但官方措辞是 `[integer_below_min_value] Invalid 'max_output_tokens'`，这里是 `must be at least 16 for this model`，所以不能算指向 OpenAI。
- 只接受 JPEG、PNG、GIF、WebP、ICO。视频在这条线上被拒，和 OpenRouter 标注的视频输入不一致；OpenRouter 线没测。
- 流式成批输出，不是每 chunk 一个 token，切分层测不了。
- 自报截止 2026-02-28，记得 2026 年 2 月的 Gemini 3.1 Pro 和 Claude 4.6；3、4 月答 UNKNOWN。这只是自述，只作旁证。
- 埋针：请求 300K、800K 都 3/3 找回（usage 分别报 337,579 和 866,952，这两个数本身是估算）。1.1M 被接受但回答为空，无法判断。

## 和 Pulse Alpha 的区别

截止时间（2026-02 对约 2025-12）、错误栈（Responses 适配对 SGLang/airlock）、流式方式、推理档位（7 档对 3 档）都不同，不是同一部署。

## 没验

- OpenRouter 线的 usage 是否也是估算值。这能区分问题在 Zen 还是上游，需要 OpenRouter key。
- 特殊 token 探针：计数有扰动，这种方法失效，没有跑。
- 音频输入。
- 1.1M 为空是上限还是模型调用了工具（中继聚合时不显示 tool_calls）。

## 工具待修

- `opencode_relay.mjs` 的 `MIN_TOKENS` 原先只在 Go 模式生效，现已对两条线都生效；本案当时用的是 scratch 补丁副本。
- 2026-11-05 发现抓包信封带有 `~/.claude/CLAUDE.md` 全文和项目路径，抓包脚本现在默认删除；此前带信封的请求很可能也发送过它们（旧信封已不在，无法核实）。
- GPT 线的 0 计数查清了：直连也出现，整份 usage 和计费都是 0，部分文本反复出现，不是中继造成的。
- 已加 `count-check`：`profile` 会先跑它，`match` 不再用不可信的计数层下判定。
- 指纹库复测（2026-11-04）：Zen 免费线和 Go 线共 24 个档案全部 `trusted`，只有 Crouton 不通过。`pixel-canary` 在 Cline 网关，没测。
- 复测同时发现 GPT 线部分请求 `prompt_tokens` 报 0（这次 96 次里重试 3 次，种子档案里 25 次有 4 次和 9 次），曾被记成 L1 的 -22。现在 L1、自检、survey 遇 0 重试，库里两个 GPT 档案的脏值已删（gpt-5.6-luna 4 个、gpt-6-luna 9 个）。
