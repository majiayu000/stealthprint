# 目录监测回测（2026-10-20）

本文用于判断"轮询模型目录 + 自动指纹"是否值得持续维护。数据和脚本都在仓库里，可以复现：

```bash
python scripts/backtest_catalog.py --cache wayback-cache --out backtest.json
```

## 数据

- Wayback Machine 对 `openrouter.ai/api/v1/models` 的 180 份有效快照，时间范围 2025-01-17 ~ 2026-10-19，共 1056 个 id，其中 585 个已从目录中消失。
- 首见日期取"最早出现该 id 的快照"和"OpenRouter 自带 `created` 字段"中较早的一个。
- 揭晓身份见 [`eval/reveals.json`](../eval/reveals.json)：只把 OpenRouter 或厂商正式确认的身份计为 `confirmed`。部署方自述且有争议的（union-alpha）、社区推测的（space-bunny）、身份未知的（cypher、aurora）都不计分。
- 发布日期优先用 model-chronicle 的 `announced`。

## 结果

**Stealth 线索：已确认的 10 个发布事件全部早于官方发布，中位提前 7 天。**

| stealth id | 揭晓为 | 首见 | 官方发布 | 提前（天） |
|---|---|---|---|---:|
| quasar-alpha | GPT-4.1 | 2025-04-02 | 2025-04-14 | 12 |
| optimus-alpha | GPT-4.1 | 2025-04-10 | 2025-04-14 | 4 |
| sonoma-dusk / sky-alpha | Grok 4 Fast | 2025-09-05 | 2025-09-19 | 14 |
| andromeda-alpha | Nemotron Nano 12B v2 VL | 2025-10-21 | 2025-10-28 | 7 |
| polaris-alpha | GPT-5.1 | 2025-11-06 | 2025-11-12 | 6 |
| sherlock-dash / think-alpha | Grok 4.1 Fast | 2025-11-15 | 2025-11-19 | 4 |
| bert-nebulon-alpha | Mistral Large 3 | 2025-11-24 | 2025-12-02 | 8 |
| hunter-alpha / healer-alpha | MiMo-V2-Pro / Omni | 2026-03-11 | 2026-03-18 | 7 |
| owl-alpha | LongCat-2.0-Preview | 2026-04-28 | 2026-06-30 | 63 |
| ox-alpha | GLM-5.3-Flash | 2026-08-20 | 2026-08-26 | 6 |

**具名 id：基本和发布同步。** 有 139 个 chronicle 模型能在目录里匹配上，提前天数的中位数是 0。提前 1 天的情况多半是 UTC 和发布地时区的差。提前 ≥ 2 天的有 8 例：Mistral Medium 3.5（22 天）、Qwen3-Max（19 天，预览版先上架）、Grok 4.5（8 天）、MiniMax M2（4 天）、GLM-4.5 / 4.5-Air、Qwen3.6-Plus 预览版、Qwen3.8-27B（各 3 天）。`qwq-32b` 被匹配到了 `qwq-32b-preview`，是名字误配，已剔除。

**召回率低。** 同一窗口里，chronicle 收录了约 197 个有具体发布日期的模型，其中只有约 14 个（7%）在 OpenRouter 上被提前看到。OpenRouter 只能看到厂商主动拿出来做匿名测试的那一小部分。

## 鉴定准确度（现有案例，还不是统计结论）

| 案例 | stealthprint 结论 | 后来揭晓 | 一致性 |
|---|---|---|---|
| omen-alpha | GLM-5 词表，SKU 对齐 GLM-5.3-Flash | 同期 ox-alpha 被确认为 GLM-5.3-Flash；omen 本身没有官方揭晓 | 一致，未独立确认 |
| union-alpha | Llama-3 词表 + Qwen2-VL 式视觉塔 | 部署方自述为 Unbiased 的 Pareto，转正后指纹连续 | 一致，身份仍有争议 |
| space-bunny | MiniMax 词表，M3 之后的新一代 | 社区推测 MiniMax M3.1-Flash；chronicle 记录 M3.1 Flash Preview 于 09-27 发布 | 一致，未确认 |
| cat-jam | Qwen3 词表，qwen3-max 类 | 未揭晓 | — |

样本太少，不能算准确率。需要用 [`scripts/blind_eval.py`](../scripts/blind_eval.py) 跑盲测：20 个已知词表的具名模型，加 5 个本地没有对应词表的负样本。

## 局限

- Wayback 快照平均每 3 天一份，首见日期是上限，短命的 id 可能整个漏掉。例如 Horizon Alpha / Beta（OpenRouter 确认是 GPT-5 早期版本）完全没被采到。改成每小时轮询可以解决。
- 只回测了 OpenRouter 一个目录。厂商自家 API 目录（OpenAI、DeepSeek 等）是另一类信号源，需要账号才能轮询，无法用 Wayback 回测。
- 部分揭晓日期来自 OpenRouter 页面的摘录或厂商在 X 上的发帖，已写进 `reveals.json` 的 sources 字段。

## 决定

- **采用**：OpenRouter stealth 线索的提前量稳定（10/10 为正，中位 7 天），值得常驻轮询。
- **待验证**：鉴定准确率需要等盲测结果；召回率需要接入厂商目录后重新评估。
- **放弃条件**：盲测中高置信结论的准确率低于 50%，就只推送"出现了新 id"，不对外给出身份判断。
