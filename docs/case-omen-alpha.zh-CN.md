# Case study: omen-alpha

[English](case-omen-alpha.md) · [中文](case-omen-alpha.zh-CN.md)

对 opencode zen 网关背后的匿名模型 `omen-alpha` 做的完整指纹分析，全程使用本库（`stealthprint`）的方法论复跑验证。案例数据：[case-omen-alpha-measurements.json](case-omen-alpha-measurements.json)。

> **TL;DR：** `omen-alpha`（经 `https://opencode.ai/zen/go/v1` 提供）使用 **GLM-5 词表**（24/24 探针精确匹配，MAE 0.00，o200k 仅 10/24）。实测上下文 ≥ **969K token**（不是传言的 500K），仅被网关 4.5 MiB body 限制封顶。服务栈是 **Rust**（"Console Go"）OpenAI 兼容网关，**不是**智谱官方接口（无 `[1210]/[1214]` 数字码，接受 `developer` 角色）。视觉**异构**：约一半多模态请求被拒（array content）；通过时是真视觉（颜色正确、开销随尺寸增长 +18 @64px → +102 @256px），但 1x1 小图会幻觉作答（编造尺寸）。

---

## 结论（2026-09-04 测量）

| 层 | 结论 | 置信度 |
|---|---|---|
| 1 分词器 | **GLM-5 词表**（GLM-5 与 GLM-5.3-Flash 的 tokenizer.json 哈希相同）。两轮独立探针 24/24 精确匹配，MAE 0.00 | 高（24/24，判别探针上 o200k 系统性偏离） |
| 2 wrapper | 固定 chat template 开销 **+36**，跨 10 种长度零漂移 | 高 |
| 3 上下文 | ≥ **969,653 token** 全部 200 OK；真实上限未触顶，仅被网关 4.5 MiB body 限制封顶（≈1M）。社区流传的 500K 不成立 | 高（二分逼近 + 针测试） |
| 3 针找回 | 595K 处 3/3、833K 处 3/3，头/中/尾精确 | 高 |
| 4 服务栈 | **Rust**（serde 报错原文），上游名 "Console Go"，OpenAI 兼容 schema（含 `developer` 角色）。**不是**智谱官方接口栈：无 `[1210]/[1214]` 数字码，`temperature: 2.0` 放行 | 高（字节级报错样本） |
| 6 视觉 | **部分可用，后端异构**。多模态 array content 约一半概率 400（serde `untagged enum MessageContent`）→ 网关后至少两种栈混挂。请求通过时是真视觉：颜色答对、开销随尺寸增长（≤64px +18，256px +102）。但 1x1 小图会答错并编造尺寸（37x40、200x200） | 中高（通过侧证据一致，拒绝率与坏后端构成随机） |

### 与社区先前结论的对照

- 「o200k 中位数 1.00」——**被推翻**。o200k 在 emoji ZWJ（21 vs 16）、中文（18 vs 14）、数字（17 vs 19）、泰文（6 vs 16）上系统性偏离。
- 「GLM-5 词表 + 固定 +24 wrapper」——词表对，**wrapper 数值不对**（本次实测恒 +36，可能因入口/模板版本不同）。
- 「500K 上下文 / 128K 输出」——上下文下限至少 969K；输出上限未验证（请求级无校验，`max_tokens: 999999` 放行）。

### 关键判别探针（API 差分 vs 各词表）

| probe | API Δ | GLM-5 | o200k | dots3 | qwen3 | minimax | deepseek | llama3 |
|---|---|---|---|---|---|---|---|---|
| emoji_zwj | **16** | 16 | 21 | 20 | 20 | 20 | 20 | 28 |
| digits | **19** | 19 | 17 | 32 | 32 | 17 | 17 | 17 |
| zh_long | **14** | 14 | 18 | 15 | 15 | 13 | 14 | 21 |
| thai | **16** | 16 | 6 | — | — | — | — | — |
| ja | **7** | 7 | 7 | 6 | 6 | 6 | 10 | 7 |
| ko | **7** | 7 | 4 | 5 | 5 | 4 | 7 | 4 |

完整原始数据见 [`case-omen-alpha-measurements.json`](case-omen-alpha-measurements.json)。

---

## 工具包使用

对任何 OpenAI 兼容端点可复跑（不限 omen-alpha）：

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"

# 目标配置
export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
export STEALTHPRINT_MODEL="omen-alpha"
export STEALTHPRINT_API_KEY="sk-..."    # 或放在 ~/.local/share/opencode/auth.json

# 拉取本地对照词表（Hugging Face 开源文件，不花一分钱 API）
./fetch_tokenizers.sh

# L1 分词器差分 —— 最便宜、最硬的证据，先跑这个
stealthprint tokenizer

# L2 模板开销常数
stealthprint wrapper

# L3 上下文上限二分 + 头/中/尾针找回（大请求，慢）
stealthprint context

# L4 错误信封家族（服务栈语言 / 校验风格）
stealthprint errors

# L6 视觉真实性（颜色 + 尺寸真值测试）
stealthprint vision
```

自定义探针：编辑 `probes.json`（`base` + `probes` 数组），差分 `Δ = T(base+probe) − T(base)` 会消掉聊天模板常数。

### 费用

全程 `max_tokens: 1`（针测试除外），本次会话 60+ 次调用，网关计费 0。对照侧全部用本地词表，**不需要**开智谱/OpenAI/小红书任何账号。

---

## 方法论（沿用并改进自牛来 / Ox Alpha 案）

1. **分词器差分**：固定底稿 + 探针，两次请求读 `usage.prompt_tokens` 相减，得到模型自己词表的计数，与本地开源 `tokenizer.json` 的 `encode()` 对照。判别探针须覆盖：英文 pangram、长中文、代码缩进、emoji ZWJ、生僻 Unicode、数字分组、泰/俄/法文。
2. **wrapper 常数**：不同长度 prompt 的 `prompt_tokens` 减本地原始计数，恒定差 = 网关模板。同一模型换入口此数会变，词表差分不变。
3. **上下文探针**：二分输入长度逼近上限（注意网关 body 限制会先挡住），再在头/中/尾埋针验证真实召回。撞线报错文案本身就是指纹。
4. **错误码家族**：畸形参数（类型错误、越界、错误 role）。看数字码、字符串码、serde/Java 报错原文、校验语言。这层认的是**服务栈**，不是权重文件名。
5. **视觉真值**：纯色图颜色问答 + 图像 token 开销随尺寸的斜率。随尺寸增长 + 颜色答对 = 真编码器；恒定开销或颜色答错 = 占位符 / 假管线。判定前须先过滤掉请求被拒的后端。

### 本案新发现（之前案卷里没有的）

- **异构后端池**：同一 model id 下，多模态 array content 约一半概率被 serde 拒（`untagged enum MessageContent`）。同一请求重试可过。怀疑网关后挂多个不同版本上游。
- **双错误信封**：OpenAI 风格（`invalid_request_error`）与 Anthropic 风格（`{"type":"error","error":{"type":"ModelError"}}`）在同一网关混用。
- **视觉分层**：能收图的后端是真视觉（颜色正确、token 随分辨率增长）；收不进图的后端上模型会看题编答案——1x1 纯红被答成 green/yellow、编造 37x40 / 200x200。判定视觉能力必须先过滤掉请求失败的后端，再判颜色正确率。

### 证据强度分级

- 定种级（可直接下结论）：分词器差分 24/24
- 栈级（认服务不认权重）：serde 报错原文、`developer` 角色、双信封
- 行为级（排除用）：针找回、颜色真值、token 斜率

### 不建议单独采信的

「你是谁」式自述、注入系统提示、审查探针、emoji 密度。stealth 模型的自我介绍经常是诱饵。

---

## 前人工作

方法延续自：`iSimplifyMe/tokenizer-fingerprint`（95 探针 + 词表拉取）、`LuD1161/ox-alpha-identification-public`（44 鉴别串案卷）、`unclecode/modelprint`（浏览器探针）。本案在这些之上新增：异构后端池检测、视觉真值协议、wrapper 常数验证法。

## License

MIT
