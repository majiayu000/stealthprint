# L1 盲测：OpenCode Go 线（2026-10-20）

本次要回答：L1 词表结论能不能信？能不能直接当成厂商判断？

## 方法

- 通过真实的 opencode CLI 1.19.2 发起请求，脚本是 [`scripts/opencode_measure.py`](../scripts/opencode_measure.py)。使用一个不带工具的最小 agent，每个探针单独 `opencode run` 一次。prompt token 数取首个 `step_finish` 事件的 input + cache.read + cache.write。基线在探针前后各测一次，两次不一致则作废。
- opencode 会把带空格的消息存成 `"…"`，末尾引号可能和最后一个字符合并成一个 token，所以本地计数也加同样的引号。不加时会在 3～4 个探针上差 1。
- 真值见 [`eval/blind_truth_opencode_go.json`](../eval/blind_truth_opencode_go.json)。开放权重模型的真值取自官方 HF `tokenizer.json`：在本地对 24 个探针计数，结果与哪个候选完全一致就记为哪个；都不一致则记为 null。没有公开词表的闭源模型作为负样本。打分只看计数 delta，评分脚本不接触模型名。
- 原始测量结果见 [`eval/results/opencode-go-2026-10-20.jsonl`](../eval/results/opencode-go-2026-10-20.jsonl)。

## 记分卡

| 指标 | 结果 |
|---|---|
| 词表识别（正样本） | **8/8**：deepseek-v4-pro、deepseek-v4.1-flash、glm-5.2、glm-5.3、glm-5.3-flash、mimo-v2.5、mimo-v2.5-pro、step-5-preview |
| 正确拒绝（负样本） | **4/4**：claude-haiku-5-5、grok-4.7、kimi-k3（本地没有对应词表）、longcat-2.0（官方词表不在候选集内） |
| 高置信结论的精度 | 8/8 |
| 厂商归属 | 正确 5，无法区分 2（MiMo 和 Qwen 共用词表），**错误 1**（Step-5-Preview 用的是 DeepSeek 词表） |
| 计数异常 | minimax-m3：两轮测量都出现负 delta，说明网关的计数和文本长度不成比例，不给出词表结论 |
| 没测成 | glm-5.1、minimax-m2.5/m2.7、kimi-k2.6、grok-4.6 不在 CLI 的模型表里；gemini-4-argon-contributor 和 muse-spark-contributor 需要在工作区隐私设置里允许"用请求数据训练"（这次没有改账号设置）；hy4-preview 超时 |

## 观察模型

| 模型 | L1 结论 | 解读 |
|---|---|---|
| minimax-m3.1-flash-free | MiniMax 词表 12/12 | 与 space-bunny 的词表结论一致。但 MiniMax 全系共用词表，L1 分不出 M3 和 M3.1，这个案例仍然只是"一致" |
| beam-free | Qwen 系词表 12/12 | 词表来自 Qwen 系（与 Qwen3 / MiMo / dots 同源），本身不说明是谁训练的 |
| mimo-v2.6-pro | Qwen 系词表 12/12 | 与 MiMo-V2.5 相同 |
| gpt-6-luna | o200k_base 12/12 | 沿用 OpenAI 的 o200k |
| qwen3.8-max / qwen3.8-flash | 不匹配（4/12） | Qwen3.8 已换新词表，与 cat-jam 案例的观察一致 |
| longcat-2.5-preview-free、hy3 | 不匹配 | 本地候选里没有对应词表 |
| omen-alpha | 无法调用 | 不在 CLI 的模型表里 |

## 结论

- **采用**：L1 词表结论可以作为高置信证据。本轮正样本 8/8、负样本 4/4，没有出现误报。
- **必须分开写**：词表结论不等于厂商判断。Step-5-Preview 直接用了 DeepSeek 词表，MiMo 和 Qwen 共用词表。对外发布时只能写"使用 X 的词表"，厂商判断需要 wrapper、上下文、错误信封、知识截止等其他层佐证。
- **新信号**：计数与文本长度不成比例（负 delta）本身就是一种网关完整性信号，应该在 watch 的证据卡里单独标出来。
- **样本仍小**：可评分的样本只有 12 个，需要用 OpenRouter 清单（`eval/blind_truth.json`）补到 25 个以上再下定论。

## 2026-11-07 复测

本次要回答：候选词表扩到 48 份后，L1 结论还准不准？能否补到 25 个可评分样本？

**没有补到 25 个。** 本机只有 OpenCode 线路，没有 OpenRouter 密钥，Go 线上有官方词表可定真值的模型不够。

真值在测量前提交（`013da2b`），由官方 `tokenizer.json` 与 48 份候选在 24 个探针上逐一比对得出。原始结果见 [`eval/results/opencode-go-2026-11-07.jsonl`](../eval/results/opencode-go-2026-11-07.jsonl)，共花约 285 个模型请求，其中盲测 267 个。

| 指标 | 按预先提交的真值 | 修正真值后 |
|---|---|---|
| 可评分样本 | 15 | 13 |
| 词表识别（正样本） | 8/8 | 9/9 |
| 正确拒绝（负样本） | 5/7 | 4/4 |
| 高置信结论的精度 | 7/9 | 8/8 |
| 厂商归属 | 正确 5，无法区分 3，错误 2 | 正确 6，无法区分 3，错误 0 |

**两个口径都要看。** 修正发生在看到结果之后，所以右栏不是纯粹的盲测。那两个「误报」是真值写错了，不是工具判错：我们把 Kimi 标成「词表不在候选内」，但 `kimi-k2` 就在候选里，它的 `tiktoken.model` 与 Kimi-K2.7-Code、Kimi-K3 的官方文件逐字节相同。修正后 `kimi-k2.7-code` 期望 `kimi-k2`，`kimi-k3`、`kimi-k3.1-pro` 改为观察。

- **没有计分：** `gemini-4-argon`、`minimax-m3` 出现负 delta，判定为计数不成比例，不给词表结论。`minimax-m3` 两轮复测都是如此。`mimo-v2.5`、`mimo-v2.5-pro` 已下线（HTTP 410）。
- **待查的异常：** `kimi-k3` 用同一份词表文件，却只对上 6/12，平均差 1.5。它的官方分词代码和 K2.7 不同，可能是这个原因，也可能是网关，本次没有分开。
- **一次 12/12 不算证明没有分歧：** `minimax-m3.1-flash` 只对上 11/12，是中置信。
- **新候选带来的变化：** Step-5-Preview 的厂商判定从「错误」变成「无法区分」，因为 `step3` 与 `deepseek` 在 24 个探针上计数相同。这是候选集变全的结果，不是判断力变强。
- **测量脚本的坑：** `opencode run` 每次会多发一个请求写会话标题，并且 stdin 未关闭时会一直等到超时。两处都已修复；旧结果的请求数因此可能是当时估算的两倍。

**响应元数据：** 通过中继对 5 个上游（DeepSeek、GLM、Claude、GPT、Grok）各发 2 个请求，响应头名称和取值完全相同，`x-zen-model` 只回显请求的模型名。响应 ID 前缀只有 DeepSeek 不同。所以在这条网关上，响应头反映的是网关而不是模型，暂不进入 `match`。多服务商网关上是否有差异没有测，需要 OpenRouter。

**决定：** 采用：L1 词表结论继续作高置信证据，样本量仍要补。待验证：25 个以上样本，以及响应头在多服务商网关上的区分能力。
