# Case study: omen-alpha

[English](case-omen-alpha.md) · [中文](case-omen-alpha.zh-CN.md)

对 opencode zen 网关背后的匿名模型 `omen-alpha` 做的完整指纹分析，全程使用本库（`stealthprint`）的方法论复跑验证。案例数据：[case-omen-alpha-measurements.json](case-omen-alpha-measurements.json)。

> **TL;DR：** `omen-alpha`（`https://opencode.ai/zen/go/v1`）使用 **GLM-5 词表**（24/24，MAE 0.00）。同网关对照具名 `glm-5*`：**不是** GLM-5 / 5.1（约 200K 上下文），**也不是** GLM-5.3 旗舰（纯文本：拒图、拒 `video_url`）。在词表、原生图+视频、图像/视频 token 开销、`reasoning_effort=low|max` 行为上对齐 **GLM-5.3-Flash**，相对具名 `glm-5.3-flash` 另有固定 **+24 token 隐身模板**（`"hi"` 37 vs 13；64×64 图 65 vs 41；小 mp4 71 vs 47）。服务路径不是同一条适配器：omen 对 `reasoning_effort=none` 返回 200；具名 Flash 返回智谱 **`[1210]`**。实测上下文 ≥ **969K**。1×1 小图仍会幻觉。后来 24 次 64×64 探针 **24/24 HTTP 200**（pt=65），其中 **16/24** 可见作答就是 `Red`/`Red.`；8/24 内容为空（`max_tokens=48` 切在 thinking 里）。0 次 serde 拒绝，0 次旗舰「不支持图」错误。

---

## 结论（2026-09-04 测量）

| 层 | 结论 | 置信度 |
|---|---|---|
| 1 分词器 | **GLM-5 词表**（GLM-5 与 GLM-5.3-Flash 的 tokenizer.json 哈希相同）。两轮独立探针 24/24 精确匹配，MAE 0.00 | 高（24/24，判别探针上 o200k 系统性偏离） |
| 2 wrapper | 相对本地 GLM 词表 **+36**（`"hi"` 37−1）。相对本网关每个具名 `glm-5*` **+24**（`"hi"` 37 vs 13）。具名 GLM 自己的 wrapper 是 +12，隐身前缀才是多出来的 24。跨长度零漂移 | 高 |
| 3 上下文 | ≥ **969,653 token** 全部 200 OK；真实上限未触顶，仅被网关 4.5 MiB body 限制封顶（≈1M）。排除 GLM-5 / 5.1（`model_max_length` 202,752）。社区流传的 500K 不成立 | 高（二分逼近 + 针测试） |
| 3 针找回 | 595K 处 3/3、833K 处 3/3，头/中/尾精确 | 高 |
| 4 服务栈 | 隐身 id 走 **Rust** Console Go 适配器。同网关上的具名 `glm-5.3-flash` **会**漏智谱 `[1210]/[1214]`。omen 吞掉 `reasoning_effort=none`（HTTP 200），且不转义 `<\|begin_of_image\|>`（Δ=1，Flash Δ=7）。`glm-5.2` 的 `effort=none` 返回 **GLM-5.3** 报错原文（网关文案拷贝） | 高 |
| 6 视觉 | 和 Flash 一样收图；`glm-5.3` 拒图（`does not support image inputs`，8/8）。64×64 红图：omen **24/24 HTTP 200**，**16/24** 内容为 `Red`/`Red.`，8/24 空内容（thinking 在 `max_tokens=48` 被切断）；Flash 8/8 HTTP 200（3 次明确 `Red`）。token 开销 65 vs 41（= +24 wrapper）。1×1 仍会幻觉。早先约 50% serde 400 **未**在该样本里复现 | 高（Flash 级视觉）；中（是否混后端） |
| 6 视频 | 原生 `video_url`（data URI 与 https）：omen 与 Flash 都答红；`glm-5.3` 拒 `video_url`。prompt_tokens 71 vs 47（data）、4014 vs 3990（https）——仍是 **+24** | 高 |
| 7 SKU | **GLM-5.3-Flash 级权重 + 约 24 token 隐身模板 + 更松的 OpenAI 适配器。** 不是目录里 `glm-5.3-flash` 的直挂别名。无法证明与 HF checkpoint 字节级相同 | 高（家族/SKU 档）；不是权重哈希 |

### 与社区先前结论的对照

- 「o200k 中位数 1.00」——**被推翻**。o200k 在 emoji ZWJ（21 vs 16）、中文（18 vs 14）、数字（17 vs 19）、泰文（6 vs 16）上系统性偏离。
- 「GLM-5 词表 + 固定 +24 wrapper」——**两套数字在不同参照系下都成立**。相对本地 GLM 词表 `"hi"` 是 +36；相对本网关具名 `glm-5.3-flash`，多出来的隐身前缀是 **+24**（具名 GLM 自己的 wrapper 是 +12）。
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

### 同网关 SKU 对照（2026-09-04 后半段）

同一 `/v1` 目录、同一把 key、同一套请求形状。具名对照：
`glm-5`、`glm-5.1`、`glm-5.2`、`glm-5.3`、`glm-5.3-flash`。

| 信号 | omen-alpha | glm-5.3-flash | glm-5.3 | glm-5 / 5.1 |
|---|---|---|---|---|
| emoji_zwj Δ | 16 | 16 | 16 | 16 |
| `"hi"` prompt_tokens | 37 | 13 | 13 | 13 |
| 64×64 红图 | 200，16/24 内容 Red，pt=65 | 200，3/8 内容 Red，pt=41 | 400 不支持图 | n/a（文本 SKU） |
| 小 mp4 `video_url` | 200，Red，pt=71 | 200，Red，pt=47 | 400 不支持 `video_url` | n/a |
| `reasoning_effort=none` | 200（被吞） | 400 `[1210]` thinking-only | 400 thinking-only | n/a |
| `<\|begin_of_image\|>` Δ | 1（裸词表） | 7（转义） | — | — |
| 已验证上下文 | ≥969K | 规格 1M | 规格 1M | 规格约 202K |

所以：词表匹配只到 **家族**，到不了 SKU。视觉 + 视频 + 1M 级上下文对齐 **Flash**，不是旗舰 5.3，也不是 5 / 5.1。隐身 id **不是** 目录 `glm-5.3-flash` 的改名：wrapper、特殊 token 加固、`[1210]` 泄漏都不同。`glm-5.2` 的 `effort=none` 甚至直接引用 GLM-5.3 报错文案。

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
stealthprint vision --repeats 24

# L6 视频模态（小 mp4 的多种 content 形状）
stealthprint video

# L7 同网关目录对照（wrapper 差、视觉、effort=none）
stealthprint catalog --family glm
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
5. **视觉真值**：纯色图颜色问答 + 图像 token 开销随尺寸的斜率。随尺寸增长 + 颜色答对 = 真编码器；恒定开销或颜色答错 = 占位符 / 假管线。判定前须先过滤掉请求被拒的后端。同一张 64×64 图重复 N 次（`vision --repeats N`）用来量化是否混后端。
6. **视频模态**：小 mp4 走 `video_url` / `type: video` / 把 mp4 当 image。原生视频 + 与图像匹配的 token 斜率是 Flash 级信号；旗舰 GLM-5.3 两者都拒。
7. **目录对照**：GET `/v1/models`，再对目标与具名兄弟跑同一套廉价 SKU 卡（`catalog --family glm`）。词表匹配只到家族；视觉/视频/`reasoning_effort`/wrapper 差才能拆 SKU。

### 本案新发现（之前案卷里没有的）

- **SKU vs wrapper vs 适配器**：GLM-5 词表在 5 / 5.1 / 5.2 / 5.3 / Flash 上是同一份。omen-alpha 在图像、视频、1M 级上下文上对齐 **Flash**，相对具名 `glm-5.3-flash` 另有固定 **+24** 隐身模板。隐身适配器更松（吞 `effort=none`、不转义特殊 token）。同网关上的具名 Flash 仍会漏智谱 `[1210]/[1214]`。
- **serde 混挂随时间变，不是混了纯文本 5.3**：早先窗口约一半多模态 array content 被 serde 拒（`untagged enum MessageContent`）。后来 24 次 64×64 红图 24/24 HTTP 200（pt=65），16/24 可见内容为 `Red`/`Red.`，8/24 空内容来自 thinking 截断。该样本里 omen-alpha **从未**返回旗舰「does not support image」。不要把 reasoning 里出现的单词 red 当成颜色命中。
- **双错误信封**：OpenAI 风格（`invalid_request_error`）与 Anthropic 风格（`{"type":"error","error":{"type":"ModelError"}}`）在同一网关混用。
- **视觉分层**：1×1 纯红仍会幻觉颜色并编造尺寸（37×40 / 200×200）。颜色正确率要在请求已被接受、且图 ≥64px 之后再判。

### 证据强度分级

- 定种级（可直接下结论）：分词器差分 24/24
- 栈级（认服务不认权重）：serde 报错原文、`developer` 角色、双信封
- 行为级（排除用）：针找回、颜色真值、token 斜率

### 不建议单独采信的

「你是谁」式自述、注入系统提示、审查探针、emoji 密度。stealth 模型的自我介绍经常是诱饵。

---

## 前人工作

方法延续自：`iSimplifyMe/tokenizer-fingerprint`（95 探针 + 词表拉取）、`LuD1161/ox-alpha-identification-public`（44 鉴别串案卷）、`unclecode/modelprint`（浏览器探针）。本案在这些之上新增：异构后端池检测、视觉真值协议、wrapper 常数验证法、视频模态探针、同网关目录对照。

## License

MIT
