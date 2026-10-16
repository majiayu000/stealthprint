# 案例报告：deepseek-v4.1-flash 满血体检（两网关）+ free 档 + fast 变体判定

[其他案例](../README.zh-CN.md#案例报告)

与前几案不同：这不是 stealth 模型鉴定，而是**具名模型完整性体检**——
网关转售具名模型的两种典型猫腻是冒名（底层换模型）与降配（量化权重）。
对 `deepseek-v4.1-flash` 在两个网关分别做了体检：
opencode zen（`/zen/go/v1`，OpenAI 兼容）与 commandcode.ai
（官方 CLI 私有协议 `POST /alpha/generate`，经开源 commandcode-proxy
转标准接口测量）。2026-10-16 追加 opencode zen **免费档**
（`/zen/v1`，`deepseek-v4.1-flash-free`）体检。原始测量数据：
[case-deepseek-v41-flash-measurements.json](case-deepseek-v41-flash-measurements.json)。

> **TL;DR：** 两个付费网关的 `deepseek-v4.1-flash` 都是**官方权重、非冒名、
> 非量化、非缩水**（~90% 置信）：词表 24/24 真身；上下文实测 opencode 916K
> （传输层天花板）/ commandcode **1,028,571**（1M 满档）；393K 输出档；
> 原生 vision。量化判定双方法：opencode 端 **logprob 双端对照 median 漂移
> 9.5e-7**（量化应为 1e-2~1e-1，差五个数量级）；commandcode 端协议无
> logprob，改用 **30 探针批量一致性 23/30 逐字符一致、7 个分叉全在低
> margin 风格点、零事实分歧**，与已钉死的 opencode 端成链。
> `deepseek-v4.1-flash-fast` = **同一权重的高吞吐部署**（词表 delta 与
> 标准版逐项全同、translate 83/83 逐字符一致、吞吐 92→132 tok/s），
> 不是量化/蒸馏小模型。
> **免费档**：词表同为 deepseek 真身（24/24 MAE 0.0，与两条付费线一致），
> 但**上下文降档至 ~288K**（付费线 916K+），且默认开 reasoning——
> **同权重、缩窗口**的免费部署。

---

## 判定表（测量于 2026-10-13 / 10-15 / 10-16）

| 层 | opencode 端 | commandcode 端 | opencode free 档 |
|---|---|---|
| 1 词表 | **24/24 MAE 0.00** deepseek（次优 minimax 11/24） | **24/24 MAE 0.00**（std 与 fast 的 delta 逐项全同） | **24/24 MAE 0.00**（排名与两条付费线全同） |
| 2 wrapper | +30，极薄 | +31，极薄（CC 信封） | +544（title 壳 2119 字符，恒定） |
| 3 上下文 | 243K–916K needle 全中；916K 为 **Cloudflare 请求体限制**（断连非 400） | 561K/841K/981K/**1,028,571** 四档全中——高于 opencode 线天花板 | ≤281K 稳定全中，≥299K 稳定 miss → **~288K 档（缩水 ~3.2×）** |
| 输出档 | max_tokens 393216/400000 均接受 | 同规格声明 | 未测 |
| 6 视觉 | 64×64 红/蓝、256×256 多色全对 → 真编码器；**坑：图片 pt 恒定 227**（网关固定计数，勿用于计量探针） | 未测 | 未测 |
| 量化 | **logprob 对照 median 9.5e-7**（两轮 n=147/126；存在完全一致样本含 reasoning 28/28）→ 同权重 | **30 探针一致性 23/30 逐字符一致**，7 分叉全为低 margin（句号/大小写/markdown 围栏/同义译法），零事实分歧 → 经链式推断同权重 | 未测（协议经 shell 后仍可带 logprobs，待做） |
| 身份 | 具名模型，无人设模板 | 同 | 同（title 壳不构成人设） |

### 免费档判定（2026-10-16，`/zen/v1` + `deepseek-v4.1-flash-free`）

| 层 | 结果 |
|---|---|
| 词表 | **24/24 MAE 0.00 deepseek**（次优 minimax 11/24，排名与两条付费线全同）→ 非冒名 |
| wrapper | +544（title 壳 2119 字符的恒定偏移，非模型差异） |
| 上下文 | ≤281K 稳定三针全中（28.6K/227.7K/281K 三档）；287-290K 边缘抖动（286.7K miss、288.5K ×2 全中、290.4K miss）；≥299K 稳定 miss（300K/303K/318K/375K/525K×2 六档）→ **有效窗口 ~288-290K（294912 级档位），约为付费线（916K+）的 1/3.2** |
| 行为 | free 部署**默认开 reasoning**（reasoning_content 流），且 title 壳会劫持输出（模型吐"标题"）；`reasoning_effort:"none"` 一并解决两者 |
| 结论 | **同权重 + 缩窗口的免费部署**：不是换模型/量化，是上下文降档 |

### 量化检测：两种方法，一条结论

- **logprob 数值对照**（opencode 端，协议支持）：同 prompt 温度 0 seed 42，
  双端取 top-token logprob 逐位比。median 漂移 **9.5e-7** 是浮点噪声量级；
  量化权重的 logit 偏移应在 1e-2~1e-1。分叉可复现于同一低 margin 点
  （FP8 batch 非确定性 + 两端模板差异），非权重差异。
  前提：**温度 0 文本一致率不可靠**（前缀/模板差异先分叉），logprob 才是
  连续指纹——文本只用于找分叉点性质。
- **批量一致性对照**（commandcode 端，协议无 logprob）：30 个多样探针，
  温度 0，与已钉死的 opencode 端比。**23/30 逐字符一致**（含弯引号、
  H₂O 下标）；7 个分叉分别是：一个句号（kn1）、一个大小写（st3）、
  markdown 围栏有无（cd4，命令逐字符同）、赋值形式（cd3，核心代码逐字符同）、
  三处同义译法（tr2/tr6/ml2）。**零事实/逻辑分歧**。量化模型在温度 0 下
  低 margin 点会大面积翻转，一致率预期 <50% 且伴随事实错误。
- **证据链**：CC ≡ opencode（23/30）＋ opencode ≡ 官方（logprob 9.5e-7，
  对照 OpenRouter provider:DeepSeek 直连）→ CC ≡ 官方。官方本身 FP8 推理，
  "非量化"指网关没有二次量化。

### fast 变体 = 同权重高吞吐部署

1. 词表差分 24/24，且 24 个 delta 与标准版**逐项全同**。
2. 温度 0 下 translate 与标准版 **83/83 逐字符一致**（含弯引号）；
   knowledge 前 117 字符逐词一致且事实全对——量化/蒸馏模型做不到。
3. 吞吐系统性更高（knowledge 92→132 tok/s、code 67→79）——大 batch /
   激进调度的部署特征。外部无法钉死具体技术（batch 调度 vs 投机解码）。

### commandcode 网关的测量路径（备忘）

- `/provider/v1/models` 是**公共端点**（无 key 也 200），不能用来验 key。
- `/provider/v1/*` 标准 API 需 Provider+ 付费档（Go plan 403
  `upgrade_required`）；Go plan 走官方 CLI 私有协议 `POST /alpha/generate`
  （CC 信封 9 键 + `User-Agent: cli`、`x-command-code-version` 等头）。
- CC 协议**有 usage 无 logprob**——词表差分可用，logprob 层不可用。
- 实测走开源 commandcode-proxy（零依赖单文件，本地标准接口）；
  Go plan **不影响上下文窗口**（档位只管端点访问权，窗口 1M 实测满档）。

---

## 复现

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"

# opencode 端（OpenAI 兼容，需 Go 订阅头）
export STEALTHPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
stealthprint tokenizer --model deepseek-v4.1-flash --tokenizers tok/
stealthprint context --model deepseek-v4.1-flash --needle-size 900000 --skip-search

# commandcode 端：先本地跑 commandcode-proxy（github.com/MAXeaglet/commandcode-proxy）
# 监听 127.0.0.1:3050 后：
stealthprint tokenizer --base-url http://127.0.0.1:3050/v1 \
  --model deepseek/deepseek-v4.1-flash --tokenizers tok/
stealthprint context --base-url http://127.0.0.1:3050/v1 \
  --model deepseek/deepseek-v4.1-flash --needle-size 1100000 --skip-search

# opencode 免费档（/zen/v1）：先用真 opencode CLI 制造一个 session id
#   opencode run --model opencode/deepseek-v4.1-flash-free "hi"   # 一次即可
#   （本地 proxy 抓包取 x-opencode-session-id，或读 ~/.local/share/opencode/）
# 再用 shell 模式测量（自动带 Bearer public + 特征头 + title 壳 + 关 reasoning）：
stealthprint tokenizer --base-url https://opencode.ai/zen/v1 \
  --model deepseek-v4.1-flash-free --tokenizers tok/ \
  --shell opencode-free --session-id ses_xxxxx
stealthprint context --base-url https://opencode.ai/zen/v1 \
  --model deepseek-v4.1-flash-free --needle-size 300000 --skip-search \
  --shell opencode-free --session-id ses_xxxxx
```

### 免费档访问机制备忘（逆向自 anomalyco/opencode 开源 console + 实验）

free tier（2026-09 起封锁匿名直连）在闭源 inference 服务做**客户端指纹校验**，
开源 console 只是透明代理。实测通过的必要特征：

1. **`Authorization: Bearer public`**（免费档哨兵 key，走匿名路径）。
2. **头组合**：`User-Agent: opencode/<channel>/<version>/<name>` +
   `x-opencode-client` + `x-opencode-project` + `x-opencode-session(-id)` +
   `x-session-affinity` + `x-session-id`。判定是"N 中命中足够多"式：单独去掉
   任一组仍过，全部去掉 403。
3. **body 双特征**：system 必须是 opencode 白名单 prompt **逐字全文**
   （title-generator prompt 2119 字符即可，user 轮随便换）**且** `stream: true`。
   等长随机 system 被拒——不是长度阈值，是内容白名单。
4. **session id 必须真 opencode 跑出来过**（服务端有注册/来源记录）：
   随机 `ses_` 伪造被拒，复用真 CLI 用过的 id 即过。
5. 传输层无关（curl 与 python urllib 都能过，TLS/JA3 不参与）。

坑：免费部署默认开 reasoning，title 壳 + reasoning 会把问答劫持成"标题"
（模型输出 `Vault codes retrieval from text` 这类）。`reasoning_effort:"none"`
同时关掉 reasoning 与劫持。stealthprint 的 `--shell opencode-free` 已内置全部配方。

logprob 对照与 30 探针一致性是临时脚本（见 measurements JSON 的
quantization 节），方法已验证，计划做成 stealthprint 的正式层
（现有各层未覆盖量化检测）。

### 成本

两轮共 ~190 次调用：opencode 端 ~60（含 logprob A/B 两轮）；commandcode
端 ~130（词表 ×2、needle 四档含 1M 级 payload ×4、三方行为 15、一致性 60）。

## 方法论笔记

1. **logprob 层的适用条件**：需要端点吐 logprobs（OpenAI 兼容线普遍支持，
   私有协议常没有）。没有 logprob 时，**批量一致性 + 分叉点定性**是次优
   但足够强的替代——分叉容易假阳性（模板差异），**完全一致不会假阳性**。
2. **测量传输路径要一并记录**：同为"1M 窗口"，opencode 线 916K 断连是
   Cloudflare 请求体限制，commandcode 线 1.028M 全中——天花板属于链路
   不属于模型，报告必须写明在哪一层受限。
3. **图片 token 恒定值（227）是计量探针的反指标**：vision 能力真，但该
   网关图片计数固定，任何依赖图片 pt 差分的探针在这家都无效。

## 许可

MIT
