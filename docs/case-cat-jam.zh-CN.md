# 案例报告：cat-jam-free

[其他案例](../README.zh-CN.md#案例报告)

对 opencode zen 网关 Go 订阅线（`/zen/go/v1`）上的匿名 stealth 模型
`cat-jam-free`（免费档的第四个匿名模型）做的完整指纹分析，全程使用本库。
原始测量数据：[case-cat-jam-measurements.json](case-cat-jam-measurements.json)。
Go 线请求需带 `x-opencode-session` / `x-opencode-client` 头（见复现）。

> **TL;DR：** `cat-jam-free` = **Qwen 家族**（高置信）：纯 Qwen3 词表
> （151,643，经特异 token 裁决）、**1M 级上下文**（842K needle 五档全中）、
> reasoning 模型、cutoff 窗口 **[2025-07-11, 2025-07-22]**（周级）。型号推断
> **qwen3-max 一类的 1M 窗口旗舰**（中置信，无词表级实锤——qwen3-max 是
> API-only，拿不到 tokenizer.json）。自报身份 "CatJam by LitterBox AI"
> 是 wrapper 模板人设的即兴表演，不作证据（三案验证过自报不可靠）。

---

## 判定表（测量于 2026-10-13/14）

| 层 | 结论 | 置信度 |
|---|---|---|
| 1 词表 | 24 探针 **24/24 MAE 0.00**，同时命中 qwen3 / mimo-v2.5 / dots3 三个本地词表（三者同源，均 151,643 Qwen 系）——文件级匹配只能到家族 | 高 |
| 1 特异 token 裁决 | `<\|mimo_audio_start\|>` 计 8 token（MiMo 词表应计 1）→ 排除 MiMo；`<no_think>` 等 dots 专属标记多 token → 排除 dots.llm；`<tool_call>`/`<\|fim_middle\|>` 单 token 命中 → **纯 Qwen3 词表** | 高 |
| 7 同网关 A/B | 具名 `qwen3.8-flash`、`qwen3.6-plus` 都已换新词表（2/24）且 delta 向量与 cat-jam 明显不同 → 排除 Qwen3.6/3.8 代 | 高 |
| 3 上下文 | needle 五档 243K/281K/430K/561K/842K，head/mid/tail **15/15 全中** → ≥842K，1M 级；排除 qwen3 标准窗 262,144 与 qwen3-next 的 512K | 高 |
| 5 知识阶梯 | 知 Kimi K2 开源（**2025-07-11**，月级复验稳定）；不知 Qwen3-Coder 月份（07-22）→ cutoff **[07-11, 07-22]**；锚点日期全部 web 核实 | 高（窗口），中（推断） |
| 2 wrapper | 恒定 **+509**（对照具名 qwen3.8 才 +61、邻居 free 模型 +8~20）→ 上游自带含人设的大模板 | 高 |
| 4 服务栈 | pydantic 风格错误信封（`[invalid_type]`/`[invalid_value]`）→ vLLM；`reasoning_effort` 被校验（unsupported_value）→ thinking 模型；developer role 200 | 高 |
| 6 视觉 | 图片 content 块在上游校验层即被拒（`[invalid_value]`）→ 纯文本，排除 VL 系 | 高 |
| 工具 | tool_choice none/auto/required 三态正确，单轮并行双工具调用 | 高 |
| 身份 | 自报 "CatJam by LitterBox AI"；问 cutoff 则拒答（"尚未公开披露"）——与 space-bunny 自报 ChatGPT 同性质：模板没给身份，模型即兴表演 | 高（行为） |

### 知识边界是软的

cutoff 不是硬墙而是**语料类型加权的渐变**：知道 Kimi K2（07-11 开源）却不知
道更早的 Grok 4（07-09）和 Meta 超级智能实验室（06-30）——「HF 开源权重 +
技术报告」类渗透深，「公司新闻 / 闭源发布」类几乎为零；gpt-oss（08-05）还能
答出"8 月"的二手碎片。这个语料加权模式本身就是 Qwen 系预训练数据的旁证。
教训：追问式验证必须带完整对话历史（单轮裸问无效）。

### 代际（推断，中置信）

1M 窗口 + 旧 qwen3 词表 + cutoff 2025-07 中，与 **qwen3-max**（2025-09 发布、
1M 上下文、沿用 qwen 词表）吻合；也可能是未公开的 qwen3.5-preview 类。
无法词表级实锤（qwen3-max API-only）。`mimo-v2.6-flash-free` 的同网关 A/B
因上游持续 "Model is unavailable" 未完成，不影响家族结论（特异 token 已裁决）。

---

## 复现

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"

export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
export STEALTHPRINT_MODEL="cat-jam-free"
export STEALTHPRINT_API_KEY="oc_sk-..."          # 需 Go 订阅
# Go 线要求 x-opencode-session / x-opencode-client 头，库默认不带，
# 用 monkey-patch 注入（与会话脚本一致）后运行：

stealthprint tokenizer --tokenizers tok/         # L1
stealthprint context --needle-size 800000 --skip-search
stealthprint errors                              # L4 vLLM 信封
stealthprint catalog --peers qwen3.8-flash,qwen3.6-plus
```

### 成本

本会话 ~120 次调用（~50 次词表探针、5 个 1M 级 needle payload、13 个
知识阶梯锚点、其余 wrapper/错误/工具/视觉）。

## 方法论笔记

1. **特异 token 裁决**：同 vocab_size（151,643）的候选家族，用家族专属
   added_tokens 单探针即可二值裁决——比整套 delta 对照便宜一个数量级。
2. **软 cutoff**：知识阶梯的分层不按时间排，而按语料类型排时，本身就是
   训练数据来源的旁证（本案：Qwen 系语料风格）。
3. **wrapper 常数对照要在同网关内做**：+509 只有跟同网关具名模型（+61）
   和邻居 free（+8~20）对比才有意义——跨网关常数不可比。

## 许可

MIT
