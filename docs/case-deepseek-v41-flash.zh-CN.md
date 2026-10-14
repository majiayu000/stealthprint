# 案例报告：deepseek-v4.1-flash 满血体检（两网关）+ fast 变体判定

[其他案例](../README.zh-CN.md#案例报告)

与前几案不同：这不是 stealth 模型鉴定，而是**具名模型完整性体检**——
网关转售具名模型的两种典型猫腻是冒名（底层换模型）与降配（量化权重）。
对 `deepseek-v4.1-flash` 在两个网关分别做了体检：
opencode zen（`/zen/go/v1`，OpenAI 兼容）与 commandcode.ai
（官方 CLI 私有协议 `POST /alpha/generate`，经开源 commandcode-proxy
转标准接口测量）。原始测量数据：
[case-deepseek-v41-flash-measurements.json](case-deepseek-v41-flash-measurements.json)。

> **TL;DR：** 两个网关的 `deepseek-v4.1-flash` 都是**官方权重、非冒名、
> 非量化、非缩水**（~90% 置信）：词表 24/24 真身；上下文实测 opencode 916K
> （传输层天花板）/ commandcode **1,028,571**（1M 满档）；393K 输出档；
> 原生 vision。量化判定双方法：opencode 端 **logprob 双端对照 median 漂移
> 9.5e-7**（量化应为 1e-2~1e-1，差五个数量级）；commandcode 端协议无
> logprob，改用 **30 探针批量一致性 23/30 逐字符一致、7 个分叉全在低
> margin 风格点、零事实分歧**，与已钉死的 opencode 端成链。
> `deepseek-v4.1-flash-fast` = **同一权重的高吞吐部署**（词表 delta 与
> 标准版逐项全同、translate 83/83 逐字符一致、吞吐 92→132 tok/s），
> 不是量化/蒸馏小模型。

---

## 判定表（测量于 2026-10-13 / 10-15）

| 层 | opencode 端 | commandcode 端 |
|---|---|---|
| 1 词表 | **24/24 MAE 0.00** deepseek（次优 minimax 11/24） | **24/24 MAE 0.00**（std 与 fast 的 delta 逐项全同） |
| 2 wrapper | +30，极薄 | +31，极薄（CC 信封） |
| 3 上下文 | 243K–916K needle 全中；916K 为 **Cloudflare 请求体限制**（断连非 400） | 561K/841K/981K/**1,028,571** 四档全中——高于 opencode 线天花板 |
| 输出档 | max_tokens 393216/400000 均接受 | 同规格声明 |
| 6 视觉 | 64×64 红/蓝、256×256 多色全对 → 真编码器；**坑：图片 pt 恒定 227**（网关固定计数，勿用于计量探针） | 未测 |
| 量化 | **logprob 对照 median 9.5e-7**（两轮 n=147/126；存在完全一致样本含 reasoning 28/28）→ 同权重 | **30 探针一致性 23/30 逐字符一致**，7 分叉全为低 margin（句号/大小写/markdown 围栏/同义译法），零事实分歧 → 经链式推断同权重 |
| 身份 | 具名模型，无人设模板 | 同 |

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
```

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
