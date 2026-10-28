# stealthprint

[English](README.md) · [中文](README.zh-CN.md)

对任何 OpenAI 兼容端点上的 stealth / 匿名模型做指纹分析的可复用库。库本身**不绑定任何模型名**——端点、模型 ID、密钥全部显式传入，换一个新模型改两个参数即可复跑同一套流程。

[第一次调查](#第一次调查先比较分词器再选择大探针) · [分层说明](#分层说明) · [案例报告](#案例报告)

## 安装

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"
# 带指纹层依赖：
pip install -e "git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"
```

## 调查档案网站

网站展示四份调查的多维指纹摘要、关键对照、未知问题和复现材料，并提供
「指纹怎么读」「使用与复现」以及 Issue #9 本地模板实验。报告和图表来自归档材料。
在仓库根目录构建并预览：

```bash
pip install -e ".[site]"
python scripts/build_site.py
python -m http.server 5572 --bind 127.0.0.1 --directory site/dist
```

打开 `http://127.0.0.1:5572`。启动前确认端口空闲。构建产物位于 `site/dist/`，
可放到静态托管服务。页面摘要与案例关联维护在 `site/cases.json`；报告正文来自
`docs/` 的中文 Markdown，图表从归档 JSON 读取，下载文件与源文件字节一致。

### 在线模型监测

[模型观察站](https://majiayu000.github.io/stealthprint/updates/) 展示公开来源变化、完整日期、来源健康、历史消息及可下载的原始依据。访客无需密钥。

首期观察 AI Tracker、OpenRouter、models.dev 的 OpenCode 目录、Hugging Face 的 DeepSeek 组织，以及 OpenAI / Anthropic 官方 SDK 模型类型文件。目录首次采集只建立基线；后续新增、修订、缺失和恢复保留前后证据。来源报告与本站实测明确区分，不自动推断模型身份或可调用性。

`model-site` 工作流每 5 分钟计划检查，部分来源每 15 分钟更新；GitHub 调度可能延迟。`monitor-data` 分支持久保存状态与 gzip 原始取证包，失败来源保留此前数据，其余来源继续运行。页面在快照停止更新时提示过期。

维护者本地复现：

```bash
pip install -e '.[site]'
git worktree add .monitor-data origin/monitor-data
python scripts/refresh_updates.py
python scripts/build_site.py
```

只读取公开元数据，不调用模型推理接口。构建本身不联网。不要同时运行多个刷新进程写同一数据目录。没有数据分支 checkout 时仍可构建历史资料预览。

[分析与来源取舍](docs/model-leak-monitoring.zh-CN.md) · [设计、验收和回滚](docs/model-monitoring-design.zh-CN.md)

## 命令行

```bash
export STEALTHPRINT_BASE_URL="https://api.example.com/v1"
export STEALTHPRINT_MODEL="mystery-model"
export STEALTHPRINT_API_KEY="sk-..."
export STEALTHPRINT_LANG=zh          # en（默认）| zh

stealthprint tokenizer --tokenizers tok/   # L1 分词器差分（先跑：最便宜、最硬的证据）
stealthprint wrapper                       # L2 模板 / wrapper 常数开销
stealthprint context                       # L3 上下文上限 + 针找回测试
stealthprint errors                        # L4 错误信封家族（服务栈语言）
stealthprint vision                        # L6 视觉真值测试（颜色 + token 斜率）
stealthprint vision --repeats 24           # L6 同一张 64×64 重复探针（混后端）
stealthprint video                         # L6 视频 content 形状（video_url / type:video）
stealthprint catalog --family glm          # L7 同网关目录对照（wrapper / 视觉 / effort=none）
stealthprint survey --out census.jsonl     # 网关普查：目录可达性分类（付费墙 / 限流 / 上游错误），
                                           # 再对可达模型跑 L1 探针（--max-models 模型数量限制、
                                           # jsonl 断点续跑、Muse 系 /responses 自动回退）
```

也可以每条命令显式传参：`stealthprint --base-url ... --model ... --api-key ... tokenizer`。全局 flag 放在子命令前后都可以。加 `--json` 后 stdout 只输出 JSON，进度写入 stderr；`--record requests.jsonl` 逐请求保存时间、输入、原始响应和错误，不记录认证头。

## 监测模型目录

```bash
stealthprint watch --state watch-state                     # OpenRouter，不需要 key
stealthprint watch --source https://api.example.com/v1/models --api-key ... --state watch-state
stealthprint watch --fingerprint --base-url https://openrouter.ai/api/v1 --api-key ... --tokenizers tok/
```

每次运行把目录保存到 `watch-state/<source>/latest.json`，并把 `added` / `removed` 事件追加到 `sightings.jsonl`。第一次运行只记录基线，不产生事件。适合放进 cron 定时跑。`--fingerprint` 会对新增 id 跑 L1 survey 并附上证据（会产生费用）；`--webhook URL` 会把新事件推送出去。sighting 只说明某个 id 何时出现，不说明它是谁做的；`stealth_hint` 只是按命名规则做的提示。[基于 180 份 OpenRouter 历史目录的回测](docs/watch-backtest.zh-CN.md)显示：10 个已确认的 stealth 发布全部早于官方发布，中位提前 7 天。`scripts/blind_eval.py` 用具名模型测 L1 结论的准确率。

## 第一次调查：先比较分词器，再选择大探针

1. 安装指纹层依赖（`stealthprint[all]`），准备[候选分词器文件](#拉取对照词表)，并设置上面的端点、模型 ID 和密钥环境变量。
2. 先看 `stealthprint tokenizer --help`，再运行 `stealthprint tokenizer --tokenizers tok/ --json`。目标 API 请求可能计费；拿返回的计数与本地候选比较不会额外调用模型。重复采样和其他层会增加请求。
3. 看候选的 `exact`、`total`、`mae`。完全匹配只表示成功探针的 token 计数差分相同，不能证明权重、架构、训练数据或厂商相同。失败探针会减少 `total`，解读排名前要确认比较的是同一组成功样本。
4. 用 `stealthprint wrapper` 补一条独立线索；如果重复计数波动，可以运行 `stealthprint tokenizer --tokenizers tok/ --repeats 3`。保存端点、模型 ID、时间、探针集和失败情况。
5. 根据尚未回答的问题选择下一层。[分层说明](#分层说明)和[案例](#案例报告)展示了如何结合词表、wrapper、上下文和模态证据。

### 结果不确定时怎么判断

- **没有候选匹配？** 候选目录可能缺少正确词表，网关也可能修改计数或在后端之间路由。先补相关的公开词表、检查重复采样，再判断身份。
- **缺依赖？** 分词器层同时需要 `tokenizers` 和 `tiktoken`，使用上面带指纹层依赖的安装方式。
- **大提示词失败就证明上下文到顶了吗？** 不能。输入长度阶梯遇到第一个错误即停止，保留此前成功点和错误原因；限流、超时和 HTTP 请求体限制都可能影响结果。报告实际成功的输入长度及失败情况，不据此认定模型的架构上限。
- **survey 免费或按金额限额吗？** `--max-models` 限制模型数量，不限制费用。少量、可续跑的目录抽样可以用 `stealthprint survey --max-models 2 --out census.jsonl`；运行前查提供商价格。续跑只跳过同一入口、探针、候选和重试设置下已完整测量的模型；失败项重试。配置不同或文件截断时明确报错。

## Python API

```python
from stealthprint import ChatClient, tokenizer_differential

client = ChatClient(
    model="mystery-model",
    base_url="https://api.example.com/v1",
    api_key="sk-...",
)
result = tokenizer_differential(client, tokenizers_dir="tok")
print(result["ranking"])     # 每个候选词表的 exact/mae
print(result["wrapper"])     # 每个候选词表对应的网关模板常数
```

每层返回纯 dict，数据键恒为英文——适合脚本化和长期对比；人类可读输出走本地化。

## 自定义探针集

```bash
stealthprint tokenizer --probes my_probes.json
```

```json
{
  "base": "You are a helpful assistant. Repeat the following text exactly and add nothing else:\n\n",
  "probes": [["my_probe", "任何能把候选词表拆开的文本"]]
}
```

内置探针集（`stealthprint/data/probes.json`）覆盖 12+ 语言：英文 pangram、中、日、韩、泰、俄、法、代码缩进、emoji ZWJ/国旗、生僻 Unicode、数字/浮点、标点。差分 `Δ = T(base+probe) − T(base)` 会消掉聊天模板常数，此结论依赖开销恒定的假设。本地也比较 `encode(base+probe)-encode(base)`，避免拼接边界误差；吻合只提供入口计数路径的线索。

## 多语言

CLI 帮助与输出支持 `en` 和 `zh`（`--lang` 或 `STEALTHPRINT_LANG`）。加新语言：在 `stealthprint/i18n.py` 里追加一个目录即可：

```python
CATALOGS["ja"] = {"cli.tok": "L1 トークナイザ差分 ...", ...}
```

无论界面语言是什么，数据键恒为英文。

## 分层说明

| 层 | 命令 | 回答的问题 |
|---|---|---|
| L1 分词器 | `tokenizer` | 这个模型的词表像哪家开源词表？最硬的证据；对照 token 全部来自本地文件，对照侧零 API 成本 |
| L2 wrapper | `wrapper` | 网关套了多少固定聊天模板开销？同一模型换入口此数会变，词表不会 |
| L3 上下文 | `context` | 成功输入长度阶梯（`--max-chars` 包含任务文本），遇错停止；头/中/尾按序精确检索 |
| L4 错误信封 | `errors` | 服务栈指纹：数字码 vs 字符串码、serde/Java 报错原文、接受哪些 role、校验风格 |
| L6 视觉 | `vision` | 核对单一颜色答案及不同尺寸的计数；固定开销不能确认编码器。`--repeats N` 记录结果分布，不推算模型混挂率 |
| L6 视频 | `video` | 是否接受原生视频（`video_url`）vs 未知 `type: video` vs 把 mp4 当图 |
| L7 目录 | `catalog` | 同网关 A/B：wrapper 差、特殊 token 差分、`reasoning_effort=none`、一张图，对照具名兄弟（`--peers` / `--family`） |

刻意**不实现**：自称探针（「你是谁」）、注入、审查探针、emoji 密度——对 stealth 模型来说自我介绍经常是诱饵。原因见案例报告。

## 拉取对照词表

```bash
./fetch_tokenizers.sh            # 下载开源 tokenizer.json 候选到 tok/
```

下载脚本把解析到的 revision 和 SHA-256 固定到 `tok/sources.lock`；下载失败保留已有文件并返回非零状态。门禁或非 JSON 格式的候选明确列为未下载，不用其他词表替代；更新研究使用新目录。

候选就是目录里的文件——把任何 `tokenizer.json` 丢进去即可新增嫌疑词表（如 `tok/mynewmodel.json`）。拉取脚本包含 Mistral-Nemo 发布的 `tokenizer.json`，已用内置探针核对其与 Tekken 文件的计数；tiktoken 的 `o200k_base` 和 `cl100k_base` 自动包含。只发布 `tiktoken.model` 的厂商（如月之暗面 Kimi）可用 tiktoken 直接构造词表——见 space-bunny 案例的复现章节。

## 案例报告

以下调查的原始请求与转录摘要完整程度各不相同：

- **cat-jam-free → Qwen 家族**（2026-10-13）：纯 Qwen3 词表（24/24 + 特异
  token 裁决出 mimo/dots），1M 级上下文（842K needle 五档全中），cutoff
  [2025-07-11, 07-22] 周级精测，+509 大模板，疑 qwen3-max 级旗舰（中置信）。
  [docs/case-cat-jam.zh-CN.md](docs/case-cat-jam.zh-CN.md)
- **deepseek-v4.1-flash 满血体检**（2026-10-15，两网关）：具名模型完整性
  检查——词表 24/24 真身，commandcode 线 1,028,571 token needle 满档；
  量化判定双方法（logprob 对照 median 9.5e-7 / 30 探针一致性 23/30）；
  `fast` 变体 = 同权重高吞吐部署。
  [docs/case-deepseek-v41-flash.zh-CN.md](docs/case-deepseek-v41-flash.zh-CN.md)
- **space-bunny-free → MiniMax**（2026-09-24）：词表 24/24（Kimi 13/24、
  GLM 7/24），同网关与 `minimax-m3`/`m2.5` delta 全等，成功输入 ≥1M；常规残差 +143，另有 +157 离群样本。具体代际、视觉架构与后端池组成仍未确认。
  [docs/case-space-bunny.zh-CN.md](docs/case-space-bunny.zh-CN.md) ·
  [English](docs/case-space-bunny.md)
- **omen-alpha → 支持 GLM-5.3-Flash 档的多层证据**（2026-09-04）：GLM-5 词表 24/24，~1M
  上下文，Flash 级图+视频，相对具名 `glm-5.3-flash` 的 +24 隐身
  wrapper，Rust 服务栈。
  [docs/case-omen-alpha.zh-CN.md](docs/case-omen-alpha.zh-CN.md) ·
  [English](docs/case-omen-alpha.md)
- **union-alpha → Unbiased Pareto**（2026-09-16）：OpenRouter 免费预览
  EOL 时官方 404 自揭真身；继任模型指纹连续（B 通道 delta 对完全一
  致），"神秘计数器"实为产品计费计数器。
  [docs/case-union-alpha.zh-CN.md](docs/case-union-alpha.zh-CN.md) ·
  [English](docs/case-union-alpha.md)

- **pixel-canary — 身份未确定**（2026-09-26）：候选词表与具名模型对照尚未确定身份，见[调查记录与测量数据](docs/case-pixel-canary.zh-CN.md)，包含失败对照和推断边界。

## 前人工作

方法延续自 `iSimplifyMe/tokenizer-fingerprint`、`LuD1161/ox-alpha-identification-public`、`unclecode/modelprint`。stealthprint 新增：异构后端检测、视觉真值协议、wrapper 常数验证法、视频模态探针、同网关目录对照、多语言探针集。

## License

MIT

## 当前测量约定

- OpenAI chat 支持全部探针；`--transport anthropic` 仅支持 `tokenizer`、`wrapper` 文本计数，并计入缓存读取/写入 token。未适配的探针在请求前拒绝。
- 重复分词器采样按轮次交错、串行执行。计数变化或样本失败时保留观测，不输出排名，也不把各条众数拼成同一后端。
- `wrapper` 是计数采样，模板差额需指定本地词表参照；`echo` 严格比较完整输出和空白，差额不直接解释为隐藏思考。
- `errors` 默认不包含巨大输出预算请求。`context` 遇错停止，也不继续发送大检索。成功长度只提供已验证下限。
- 离线重算 L1 时，将保存的 `probes`、`base_api_prompt_tokens`、`api_delta` 传给 `tokenizer_differential(None, ..., base_api=..., api_delta=...)`，使用同一组候选文件。
- survey 新记录绑定实验配置；旧版无配置记录明确拒绝，请使用新输出文件，不自动迁移历史数据。

Issue #9 已于 2026-09-29 关闭，词表补充已合并；官方受限模板对照及实时思考开关实验未完成，也不再针对退役预览继续计划。[最终处置](https://github.com/majiayu000/stealthprint/issues/9#issuecomment-5889595968)。
