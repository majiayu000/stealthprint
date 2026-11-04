# crouton-alpha：usage 计数不可用，身份未定（2026-11-04）

**决定：待验证。** 没有指认出厂商；这次只确定了它和 Pulse Alpha 不是同一个部署，以及词表类证据在这个模型上失效。

要支撑的决定：能否把 Crouton 定位到具体厂商。放弃条件：真实词表计数拿不到，且错误和行为层没有唯一指向时停止，改等官方揭晓或具名模型对照。

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

- `opencode_relay.mjs` 的 `MIN_TOKENS` 只在 Go 模式生效，这次用 scratch 副本补到免费模式。
- `profile` 和 `match` 不检查计数是否可信，建议加单字母同价和可叠加两项自检，不通过就把 L1、视觉标为无效。
