# Pixel Canary：24 条探针已完成，身份尚未确定

测量日期：2026-09-26。入口：Cline API，模型 `stealth/pixel-canary`。

**结论：累计比较 21 套本地候选词表，并通过官方 CLI 成功对照 5 个具名模型（各含 4 条不同文本的短探针），均未找到完整匹配；供应商和具体模型仍未确定。** 这轮没有证据支持“就是 Gemini”，也不能把“不匹配某套公开词表”扩大成“排除了这个厂商的所有新模型”。

完整请求、响应、差分和词表哈希见 [测量 JSON](case-pixel-canary-measurements.json)。请求均为合成文本，没有上传项目源码。未改动库的核心代码、README 或用户配置；未提交或发布。

## 方法与有效性

- 复用 `ChatClient.prompt_tokens`、内置 24 条多语言探针及 `layers.tokenizer_differential`。
- 实验适配器显式设置 `stream=false`，解包 Cline 的 `data` 响应信封；API 密钥只在进程内使用，没有写入记录。
- 最终统一参数：`max_tokens=256`、`reasoning_effort=none`，最多 3 个并发请求。
- 原先 1 / 64 token 输出预算下出现过 `empty response content`，也有网络错误。两项参数共同调整后取得有效样本，未做单变量因果验证，不能断言只有输出预算导致失败。
- 45 秒超时曾漏掉慢响应，补采时延长到 120 秒。主实验 27 次成功请求耗时 4.827–63.011 秒，中位数 32.952 秒；这包含网络、网关与生成时间，不是纯模型吞吐。
- 基线在开始和末尾共测三次，输入计数都是 **178**。24 条探针最终全部获得有效计数。其余探针没有逐条重复，因此不能据此保证不存在混合后端。
- 差分定义为 `T(base + probe) - T(base)`。另行计算本地的同上下文差分 `encode(base + probe) - encode(base)`，避免把边界分词差异误判成身份差异。

## 输入侧结果

| 本地候选词表 | 精确匹配 | 平均绝对误差 |
|---|---:|---:|
| GLM-5 / GLM-5.3-Flash | 各 4/24 | 3.750 |
| Mistral Small 4 | 4/24 | 2.417 |
| Llama 3 | 3/24 | 3.083 |
| Gemma 3（Google SDK 使用的公开词表） | 3/24 | 3.167 |
| OpenAI o200k_base | 3/24 | 3.750 |
| Llama 4 | 3/24 | 3.917 |
| Qwen3 / MiMo-V2.5 / dots3 | 各 2/24 | 2.750 |
| Gemma 4 | 2/24 | 3.208 |
| OpenAI cl100k_base | 1/24 | 4.375 |
| MiniMax | 1/24 | 4.667 |
| DeepSeek | 0/24 | 4.292 |
| Kimi K2 | 0/24 | 4.375 |

这些低匹配率不能用来选一个“最像的赢家”。Qwen3、MiMo-V2.5、dots3 对这套普通文本探针的本地计数彼此相同，单个中文样本的命中不具备厂商区分力。后续完整数据也不支持它们，所以未继续进行原先考虑的特殊标记鉴别。

部分实测差分：

| 探针 | 输入 token 增量 |
|---|---:|
| 英文 pangram | 11 |
| 中文长句 | 15 |
| 代码 | 20 |
| emoji ZWJ | 33 |
| 数字串 | 33 |
| 日文 | 7 |
| 韩文 | 6 |
| 泰文 | 6 |
| 浮点数字 | 32 |
| emoji 国旗 | 36 |
| 纯空白 | 0 |

纯空白增量为 0，而本地候选并非如此。网关去除尾部空白、特殊计数规则或不同分词器均是待验证解释；不能直接认定是哪一种。未匹配到词表，因此也没有确定真实 wrapper 的 token 长度；JSON 中的 `baseline_overhead` 只是各候选假设下的差值。

## 输出侧与元数据

24 条探针均返回 `finish_reason=stop`、`reasoning_tokens=0`。其中 23 条在去除首尾空白后精确回显；纯空白样本没有按原样回显。对原有 12 套候选计算实际可见输出的 token 数，未出现全样本恒定偏移。这个检查辅助支持“不匹配原有候选”，但不独立证明计数器就是底层模型的真实分词器。

主实验的 **27 次成功响应有 27 个不同的 `system_fingerprint`**。三次相同基线也拿到不同字符串，但 token 计数一致。可观察结论是该字段在本次入口不稳定；不能由此证明存在 27 个模型，也不能把字符串反查当作身份鉴定。

单独的中文流式对照在 68.937 秒完成，输入 193、输出 15 token，与主实验中文探针一致。它使用 64 token 输出上限和精简请求头，属于补充证据，没有混入主实验排名。

## 具名对照与费用边界

尝试同网关 `cline-free/gemini-3.8-flash`，9 条记录均返回 403：该免费模型只允许通过 Cline 产品界面调用，旧客户端需要更新。没有伪造客户端标识绕过限制，也没有改用付费模型；第一轮未完成具名 Gemini A/B；后续同网关短探针对照已在文末补齐。

第一轮共有 59 条已落盘的请求尝试记录：28 次成功、8 次 HTTP 500、9 次 HTTP 403、14 次连接错误或超时。其中 403 全部来自 Gemini 对照；部分手动停止时仍在途的请求未形成完整记录，因此 59 不是精确的总发起次数。不同参数、重试和网络故障混在其中，不能把这个比例当作服务可用率。

28 次成功的 Pixel Canary 请求共报告输入 **5,418**、输出 **574** token，报告费用合计 **$0**。这是本次成功响应的 API 元数据，不是账户账单核对；失败或中断请求没有完整 token 记录。

## 公开词表来源与后续

原有候选的本地 SHA-256 已记录在测量 JSON 中。新增来源均为官方公开文件，并核验下载哈希：

- [Google SDK 的本地词表映射及 Gemma 3 固定版本下载地址](https://github.com/googleapis/python-genai/blob/main/google/genai/_local_tokenizer_loader.py)。映射同时说明多种 Gemini 模型共享词表，词表匹配本来也不能区分具体代际。
- [Google Gemma 4 31B tokenizer.json](https://huggingface.co/google/gemma-4-31B/blob/main/tokenizer.json)，SHA-256 `12bac982b793c44b03d52a250a9f0d0b666813da566b910c24a6da0695fd11e6`。
- [Mistral Small 4 固定提交 tokenizer.json](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603/blob/90dc2e6225c417f77ebdc8ed30160fbb2f6aea96/tokenizer.json)，SHA-256 `2ba5b3330fd84d5376fcca797cfb3b42eee6241ce23e3271e6fb2a115a8751bd`。

还查看了 Anthropic 旧版公开 tokenizer；[官方说明](https://github.com/anthropics/anthropic-tokenizer-typescript)明确指出它从 Claude 3 起已不准确，因此未将其作为现代 Claude 身份对照，也没有把它计入上述 15 套候选。

第一轮提出的后续证据是（Cline Gemini 对照已在文末补齐）：从受支持的 Cline 产品入口取得当前具名 Gemini 的同探针对照；或取得其他候选的官方输入计数结果，同时控制空白归一化和网关模板。未完成这些对照之前，鉴定状态保持 **unknown**。


## 追加补测：21 套候选，OpenCode 实际调用受阻

用户授权继续使用 OpenCode 后，追加六套官方词表，本地计数复用第一轮已完成的 24 条 Pixel Canary 探针，没有新增 Pixel Canary API 样本。通过 `stealthprint.layers.tokenizer_differential` 及独立的同上下文差分计算交叉复核。原始版本、SHA-256、逐条差分和 CLI 结果见[补测 JSON](case-pixel-canary-supplement-measurements.json)。

| 新增候选 | 匹配数 | MAE |
|---|---:|---:|
| LongCat-2.0 | 0/24 | 3.417 |
| LongCat-Next | 0/24 | 3.792 |
| 腾讯 Hy3 | 0/24 | 3.917 |
| Ling-3.0-tiny-base | 3/24 | 2.667 |
| Nemotron-3-Super-120B-A12B-FP8 | 4/24 | 2.417 |
| Step-3.5-Flash | 0/24 | 4.292 |

上述旧版或公开词表不能替代 LongCat-2.5、Step-5、Ling-3.0-Flash-Fin、Nemotron-3-Ultra 等具名新模型的实际对照。累计 21 套候选仍无完整匹配，身份保持 **unknown**。

实际启动了官方 `opencode-ai` CLI **1.18.32**，通过临时 npm 入口运行，未进行全局安装。默认本机配置及 CC Switch 的 OpenCode provider 均未找到模型登录凭据；实验使用独立 XDG 目录，关闭分享，仅提交合成文本和一行合成 JavaScript。

CLI 目录列出免费 LongCat 2.5、Ling 3.0、Nemotron 等模型。LongCat 首次直连遇到证书校验错误；在不关闭 TLS 校验、不改全局代理设置的前提下，使用现有本地代理重试，服务端返回 `FreeTierError` / HTTP 403，提示免费层只能在 OpenCode 内使用。随后实际尝试 `nemotron-3-ultra-free`、`ling-3.0-flash-fin-free`，也收到相同 403。最后换成 OpenCode 默认 build agent 读取合成文件，LongCat 仍收到同一个 403。

因此本次是 **3 个不同具名模型的连通性尝试，0 个成功完成 API 指纹对照**。不能推断模型本身不支持请求，或用户已登录账号一定无权限；这条未登录隔离 CLI 路径的识别/授权原因仍未查明。没有伪造客户端标识、关闭证书校验或切换付费模型。上述为初次失败阶段的判断；后续在没有新增登录或密钥的情况下恢复调用，修正见下一节。


## OpenCode 入口恢复：四个具名模型的实际对照

入口已恢复，并完成有效短探针对照。[原生客户端测量 JSON](case-pixel-canary-opencode-measurements.json)包含有效批次、被剔除批次、请求结果、缓存计数及局限。此前补测 JSON 中的“0 个成功对照”描述的是入口恢复前的阶段，已被本节更新。

### 403 与固定上下文

官方客户端在工具权限为 `deny` 时返回 FreeTierError；改成默认 build agent、工具权限 `ask`（外部目录仍 deny），无需新增登录或密钥即可生成回答。公开 [issue #50081](https://github.com/anomalyco/opencode/issues/50081)也包含相关权限触发案例。这支持权限过滤影响入口识别的解释，但没有检查服务端校验实现，不把具体机制当作已证实事实。此前把登录缺失当作必需解决条件的判断不充分。

第一批虽然成功调用了模型，但发现 OpenCode 仍自动加载全局 Claude 指令和技能，基线发生漂移。例如 LongCat 的起止基线为 28195、28199，中文样本甚至低于起点。该批次作为诊断数据保留，**全部剔除出输入指纹排名**。

后续仅对实验进程设置 `OPENCODE_DISABLE_CLAUDE_CODE=true`、`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=true`、`OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=true`；加载技能数由 159 降至 4。没有修改用户全局配置、网络线路或证书信任。每个模型重新检查开始和末尾基线。

### 有效结果

数值为输入计数相对各自基线的增量：

| 模型/入口 | 重复基线 | 中文 | emoji ZWJ | 数字串 | 泰文 | 与 Pixel 相同 |
|---|---|---:|---:|---:|---:|---:|
| Pixel Canary / Cline（第一轮） | 178 / 178 / 178 | 15 | 33 | 33 | 6 | — |
| LongCat 2.5 Preview / OpenCode | 7892 / 7892 / 7892 | 14 | 20 | 32 | 18 | 0/4 |
| Ling 3.0 Flash Fin / OpenCode | 8540 / 8540 | 12 | 22 | 32 | 8 | 0/4 |
| Nemotron 3 Ultra / OpenCode | 8621 / 8621 | 27 | 37 | 32 | 8 | 0/4 |
| Muse Spark 1.3 Contributor / OpenCode | 8189 / 8189 | 14 | 20 | 17 | 6 | 1/4 |

四个具名模型均取得全部四条探针，各自重复基线稳定；未发现完整一致的增量向量。LongCat、Ling、Nemotron 为 0/4；Muse 仅泰文为相同计数，1/4 不构成身份匹配证据。

计数口径为 OpenCode `step_finish.tokens.input + cache.read + cache.write`，避免缓存命中时只看未缓存输入。只纳入退出码 0、单步完成、无工具调用事件的请求。一条 Nemotron emoji 请求因本地 `database is locked` 失败，批次完成后串行重试成功，失败记录仍保留。

### 边界

- 这是 **4 个实际模型 × 4 条短探针**，不等于每个模型完成了全部 24 条测试，也不与 21 套本地词表数量简单相加。
- 目标来自 Cline，具名对照来自 OpenCode；基线抵消固定模板开销，但不能完全排除跨网关预处理或计费计数差异。结论针对本次可见计数路径，不能断言权重或 checkpoint 一定不同。
- Ling 某些生成侧计数与可见文本不一致（有文本却记 0 输出 token），因此没有使用这一路径的输出侧计数认定身份。
- 本次没有新增 Pixel Canary 请求，使用第一轮保存的目标计数。本节阶段尚未完成 Gemini、Claude、GPT 的具名 API 对照；Gemini 的后续结果见下一节。
- OpenCode 事件中的 cost 均为 0；这些是客户端报告/计算值，没有据此声称完成账单审计。

当前鉴定状态继续保持 **unknown**。


## Google 补测完成：同一个 Cline 客户端和网关

2026-09-27 更新：已使用官方 Cline CLI **3.0.65**，在同一 `cline` provider 下实际调用 `cline-free/gemini-3.8-flash` 与 `stealth/pixel-canary`。未新增付费模型调用。详细请求、响应和验证结果见 [Google 对照 JSON](case-pixel-canary-google-measurements.json)。累计为 **21 套本地词表 + 5 个具名模型的实际短探针对照**，这两个数量不能简单相加成 26 个不同模型。

Gemini 原先直接 API 的 403 在使用真正的 Cline 官方产品客户端后不再出现。Google 自家的 Gemini CLI 旧缓存要求重新认证，未取得模型回答，没有把该尝试计入成功模型数。官方原生 Cline npm 包按 registry 的 SHA-512 integrity 校验后使用；未全局安装客户端。

### 选择非空基准，避免误把退出码 0 当作完成

Gemini 的两个空文本基线请求都触发追问，`run_result.finishReason=aborted`，即使进程退出码是 0，也不计作完成的有效对照。最终只取 `finishReason=completed`、单次迭代、无工具调用的样本，并重复中文长句作为共同基准。

Cline SDK 的 [usage 实现](https://github.com/cline/cline/blob/main/sdk/packages/core/src/services/usage.ts)说明 `inputTokens` 已包含缓存部分。本轮直接使用 `run_result.usage.inputTokens`，没有重复加 cache 字段；这一口径与前面的 OpenCode 字段求和方式不同。

| 样本输入计数 | Gemini 3.8 Flash | Pixel Canary |
|---|---:|---:|
| 中文长句（第一次） | 3775 | 5986 |
| 中文长句（重复） | 3775 | 5986 |
| emoji ZWJ | 3773 | 6004 |
| 数字串 | 3791 | 6004 |
| 泰文 | 3765 | 5977 |

绝对值包含各模型的模板开销，不能直接对比大小。相对相同中文样本做差：

| 相对中文基准的差分 | Gemini 3.8 Flash | Pixel Canary |
|---|---:|---:|
| emoji ZWJ | -2 | +18 |
| 数字串 | +16 | +18 |
| 泰文 | -10 | -9 |

**三项差分 0/3 一致**，并且两侧重复中文基准各自完全稳定。Pixel Canary 在新 CLI 路径下相对其正常完成的空基线，仍得到中文 15、emoji 33、数字 33、泰文 6，与第一轮直接 API 的四条结果一致。

这组证据不支持 Pixel Canary 与本次 Gemini 3.8 Flash 端点具有相同计数指纹。它不能排除 Google 使用不同词表、不同计数方式的其他模型，更不能识别未公开的代际或具体 checkpoint。同客户端、同网关减小了跨网关差异，但仍保留模型专属预处理/计数层的可能性。当前身份继续保持 **unknown**。
