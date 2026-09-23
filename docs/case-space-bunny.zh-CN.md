# 案例报告：space-bunny-free

[English](case-space-bunny.md) · [中文](case-space-bunny.zh-CN.md)

对 opencode zen 网关上的匿名 `-free` 模型 `space-bunny-free`（公共线
`/zen/v1` 与 Go 线 `/zen/go/v1` 均有列出）做的完整指纹分析，全程使用本库
（`stealthprint`）。原始测量数据：
[case-space-bunny-measurements.json](case-space-bunny-measurements.json)。
与前两案不同：免费层现已客户端门禁（裸 API 调用 403 `FreeTierError`），
以下所有数据点来自 Go 线的有效 Go 订阅。该模型**不在 OpenRouter 上**
（已查 456 个模型的目录）。

> **TL;DR：** `space-bunny-free`（`https://opencode.ai/zen/go/v1`）使用
> **MiniMax 词表**（24/24，MAE 0.00），**不是 Kimi**（13/24）也不是
> GLM（7/24）。同网关 A/B：其 24 探针 delta 向量与 `minimax-m3` 和
> `minimax-m2.5` **完全一致**（这俩彼此也一致——MiniMax 全系共用词表，
> 词表证据是家族级的）。Kimi 假设单独证伪：`kimi-k3`/`kimi-k2.7-code`/
> `kimi-k2.6` 共享一种签名，与 space-bunny 在 14/24 个探针上分离。隐身
> wrapper：固定 **+143 token** chat template，10¹→10⁶ token 零漂移。上下文
> ≥ **1M token**（100K/204.6K/450K/1M 阶梯全部 200 OK，`pt = 本地计数 +
> 143` 精确），**排除 m2.5 代**（其端点帽 204,800）；200K 深度三**异码**针
> 检索 3/3 逐字命中。视觉是适配层级的：收图，开销 **+34 恒定、与分辨率
> 无关**（1×1 == 64×64），64×64 颜色 8/8 正确，退化 1×1 幻觉 1/4。后端池
> **同构**（14 个同 payload 14/14 状态/pt/形状全同——没有 omen 式 50/50
> 混池）。被问是谁时，它即兴自报 **"I'm ChatGPT, made by OpenAI"**——
> 隐身模板不提供身份（其思考链自己泄露了这一点）。实际知识 ≥ 2025-11
> （知道 Claude Opus 4.5 的月份，`minimax-m3` 不知道）且 < 2026 年中 →
> 与 **M3 之后的新一代或 M3-refresh stealth 预览**一致（推断，中置信）。

---

## 判定表（测量于 2026-09-24）

| 层 | 结论 | 置信度 |
|---|---|---|
| 1 词表 | **MiniMax 词表**，对本地 M1 tokenizer.json 24/24 全对，MAE 0.00（MiniMax 各代共用）。Kimi 13/24 MAE 1.62；GLM 7/24（最差）；llama4 13/24 | 高 |
| 7 目录 A/B | delta 向量与 `minimax-m3`、`minimax-m2.5` 24 探针**全等**。Kimi 三兄弟（k3/k2.7-code/k2.6）彼此全等但与 space-bunny 差 14/24。管线交叉验证：kimi-k3 的 API delta 对本地构造的 Kimi K2 tiktoken = 23/24 | 高 |
| 2 wrapper | **+143** 固定 chat template，16→1M token 五个数量级零漂移（810K 针点一次 +14 离群，BPE 拼接边界效应）。base_pt：space-bunny 159 / m3 192 / m2.5 57——三种不同模板 | 高 |
| 3 上下文 | ≥ **1M token**（阶梯 100K/204.6K/450K/1M 全 200 OK；每级 `pt = 本地 + 143`）。**排除 m2.5 代**（端点帽 204,800，其错误文本自引）。上界未探到 | 高 |
| 3 针检索 | 200K 深度、三个**异码**（1/3、2/3、末尾）：3/3 逐字按序命中。（810K 针因同秒同码 bug 证据力弱，但深度读取成功） | 高（200K） |
| 4 服务栈 | 错误信封归一化：所有畸形 payload → 统一 `[invalid_request_error] invalid request`，零泄露。对照：m3 泄露 MiniMax 原生码（`(2013)`、`model[MiniMax-M3] does not support max tokens > 524288`），m2.5 是另一翻译层，kimi 是 pydantic 风格。四种栈；space-bunny 的是带盾的 | 高 |
| 6 视觉 | 适配层级：+34 pt 开销**与分辨率无关**（1×1 == 64×64，与 omen 的 +18 同属假 ViT 签名）。64×64 红 4/4、蓝 4/4 正确；1×1 红 3/4 "Red" + 1/4 "Black"。m3 也收图但签名不同（−1/+7）；m2.5 **无视觉路由**（404） | 高 |
| 后端池 | 14 个同 payload（7 文本 `temperature=2.0` + 7 图）：14/14 HTTP 200，pt/形状/`name` 全同 → **同构单栈**（对照 omen 的 50/50 serde 分裂） | 高 |
| 身份 | 自报 **ChatGPT/OpenAI**；思考链泄露 "no model identity provided… need adhere system/developer"。每条消息带 `name: "Space Bunny"` 字段（网关唯一）。电报体 M2 系思考风格 | 高（行为），不涉权重 |
| 知识 | ≥2025-11（Opus 4.5 月份；m3 不知），知道 GPT-5 精确日期（m2.5 不知），不知 2026 年中（Fable 5、Kimi K3、GLM-5.3）。自报 "June 2024" cutoff 是即兴编的 | 高（事实），中（推断） |

### 代际（推断，中置信）

词表证明家族（MiniMax）但不能定代。代际判断组装自：上下文 ≥1M
（m3 级；杀掉 m2.5）、实际知识 ≥2025-11（比 m3 的实际覆盖更新）、M2 系
电报体思考、+143 模板独立于 m3 的 +178 / m2.5 的 +37——独立服务路径。
读作 **M3 之后的新一代或 M3-refresh stealth 预览**，符合网关命名传统
（`omen-alpha` = GLM-5.3-Flash，`union-alpha` = Unbiased Pareto，
`space-bunny` = MiniMax）。无法证明 checkpoint 同一性。

---

## 复现

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"

export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
export STEALTHPRINT_MODEL="space-bunny-free"
export STEALTHPRINT_API_KEY="oc_sk-..."   # 需 Go 订阅；免费层已客户端门禁

./fetch_tokenizers.sh                    # 9 个词表；Kimi 需 tiktoken.model（见下）

stealthprint tokenizer                   # L1 —— ~25 次调用定家族
stealthprint wrapper                     # L2
stealthprint errors                      # L4
stealthprint catalog --family kimi       # L7 对具名同门 A/B
```

Kimi 对照词表：月之暗面不发 `tokenizer.json`（K2/K2.5/K3 只有
`tiktoken.model` + `tokenization_kimi.py`）。直接用 tiktoken 构造：

```python
from tiktoken import Encoding
from tiktoken.load import load_tiktoken_bpe
enc = Encoding(name="kimi-k2", pat_str=KIMI_PAT_STR,          # 取自 tokenization_kimi.py
               mergeable_ranks=load_tiktoken_bpe("tok/kimi-k2.tiktoken"),
               special_tokens={})
```

会话内验证：kimi-k3 的 API delta 对此构造 23/24 全对。

### 成本

本会话 ~290 次调用（≈125 次 A/B 词表探针、≈35 次补测含 4 个 1M 级
payload 各 ≈1.4 MB）。对照侧计数全部来自本地文件。

---

## 方法论笔记（扩展 omen/union 手册）

1. **同网关 A/B 把 L1 从"文件匹配"升级为"活端点匹配"**：与具名同门
   （`minimax-m3`/`m2.5`）delta 向量全等，证明的是整条计数路径而不只是
   词表文件。
2. **上下文上限即代际指纹**：204,800 vs 1M 是便宜且硬的 m2.5 代 / m3 代
   分层（一发 450K 探针即可裁定）。阶梯数据兼作 wrapper 恒定性测量
   （每级 `pt − 本地`）。
3. **带日期事件的知识阶梯**（R1 2025-01 → GPT-5 2025-08 → Opus 4.5
   2025-11 → 2026 年中未知项）：干净分层 m2.5/m3/space-bunny；**自报**
   cutoff 是噪音（三个模型都报错）。
4. **池同构探针**：N 个同 payload 比对状态/pt/形状方差——本案 14/14
   全同，是 omen 案的反例结果。
5. **Bug 记录**：lambda 时间戳生成多针必须加随机源——本会话同秒同码
   bug 在修正重跑前复现过两次。

## 许可

MIT
