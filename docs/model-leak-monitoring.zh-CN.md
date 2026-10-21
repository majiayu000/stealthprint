# 模型泄露监测：来源核验与最小实施方案

本文记录实施前的调研。后续设计与验收见 [上线设计](model-monitoring-design.zh-CN.md)。

调研日期：2026-10-20（北京时间）。网络抽样时间：01:48–01:49。

**决定：采用少量直连来源监测变化，AI Tracker 补充公开线索，stealthprint 按需复测。** 第一轮复用已有静态网站和 Python 采集代码，暂不建设通用爬虫平台、数据库服务或自动模型身份判定系统。

本次“泄露”指未正式宣布的模型名称、公开目录条目、SDK 标识、公开配置及匿名预览线索。目标是帮助读者判断“什么发生了变化、依据在哪里、是否值得进一步测量”。本次完成了来源调研和只读抽样；持续监测、通知与网站部署尚未实施。

## 1. 要支撑的决定与放弃条件

要决定的是：哪些来源值得长期接入，能否在可接受的人工复核成本下，提供比单纯转载更早或证据更完整的消息。

建议试运行 7 天，记录每个来源的成功采集、有效变化、重复消息、误报、人工复核时间，以及相对聚合站的发现时间差。这些是待验收指标，不是本次已测出的效果。

- 原始证据与来源时间可追溯；旧记录不能因后续发布、改稿或撤回而消失。
- 同一内容重复采集不产生新消息；首次建立目录基线不把全部历史模型当成新发布。
- 采集失败显示来源故障和上次成功时间，不产生“全部模型下架”的假消息。
- 新来源若连续 7 天只有重复内容、没有提前量或新增证据，同时造成明显维护成本，则降低频率或移除。没有发生新模型事件时，记为样本不足，不能据此判定失败。
- 任何必须猜测模型身份、依赖未获授权的登录态，或无法留下证据的来源，不进入首期自动采集。

## 2. 已核对的仓库状态

GitHub 上 #24、#22、#23 的合并顺序正确。`v0.1.0` 已于北京时间 2026-10-20 01:29:52 发布，指向 main 的 `86c6f8d72cfaf010cf93ab54b15ec539e0910850`，该提交的最新 checks 成功。此次没有重做发布或重新跑完整发版校验。[Release](https://github.com/majiayu000/stealthprint/releases/tag/v0.1.0) · [main CI](https://github.com/majiayu000/stealthprint/actions/runs/39294345806)

当前 `feat/model-updates-site` 工作区已有以下未提交内容；远端 main 的树中没有模型动态采集模块，因此这些能力不属于已发布的 v0.1.0：

| 本地能力 | 当前实现与边界 |
| --- | --- |
| AI Tracker 采集 | `stealthprint/updates.py` 读取 JSON Feed 和模型目录；两次请求成功后才替换快照 |
| 消息历史 | 按来源事件 ID 合并，保留离开最新 Feed 的历史事件；同 ID 改稿会覆盖旧内容 |
| 页面 | `/updates/` 按模型分组，区分外部消息与本站测量，显示采集日期 |
| 刷新入口 | `scripts/refresh_updates.py` 手工刷新，`scripts/build_site.py` 离线构建 |
| 自动化 | 仓库工作流只有 checks；没有定时刷新或发布工作流 |

当前 `site/updates.json` 采集于 2026-10-17 21:53:17 北京时间，包含 49 条消息、124 个目录条目。它是历史快照，本次未用网络失败结果覆盖。

现有采集模块的 4 项单元测试本次全部通过，覆盖失败保留、事件历史、分组不修改输入和链接协议校验。这只验证已有逻辑，不证明持续服务可用。

## 3. 来源取舍与本次实测

| 来源 | 本次证据 | 能说明什么 | 采用决定 |
| --- | --- | --- | --- |
| [AI Tracker](https://live.aitracker.bot/developers) | 文档可读；Feed、model-pages、model-news 均返回 HTTP 530。再次读取 Feed，错误体为 Cloudflare 1033，`Retry-After: 120` | 聚合站发布了什么线索及后续状态；不等于我们直接观察到上游变化 | 保留为补充来源；当前故障须单独展示 |
| [OpenRouter Models API](https://openrouter.ai/docs/api-reference/models/get-models) | 未发送认证头，HTTP 200；470 个唯一 ID，`total_count=470`，`links.next=null` | 该网关目录出现、修改或移除了条目 | 首期采用，优先监测 ID 和指定元数据差分 |
| [models.dev](https://github.com/anomalyco/models.dev) | 未发送认证头，HTTP 200；232 个 provider、9,078 个 provider-model 条目；带 ETag 的后续请求返回 304 | 社区目录记录了某种配置或状态；不是 9,078 个独立底层模型，也不是可调用清单 | 首期采用作目录线索；保留来源归属与 deprecated 状态 |
| [Hugging Face Hub](https://huggingface.co/docs/hub/en/api) | `deepseek-ai` 的模型列表请求 HTTP 200，返回限定的 3 条记录和下一页 Link | 发布者组织的公开模型仓库及元数据发生变化 | 首期仅覆盖少数明确关注的组织；本次只抽样 DeepSeek |
| 官方 SDK 仓库 | 通过 GitHub API 读取 OpenAI、Anthropic SDK 的固定提交文件成功 | 官方代码中出现了标识、参数或废弃说明 | 首期监测少量模型类型文件的提交差分 |
| [OpenCode 官方模型文档](https://opencode.ai/v2/docs/console/models/) | 文档可读；由已知 API base 拼出的 `/models` 路径本次返回 404 | 官方页面描述的模型目录；不能假定所有兼容服务都实现 `/models` | 文档作交叉核验；该 404 路径不纳入监测 |
| 公开网页脚本、配置文件 | 本次未验证能稳定提供新增泄露的具体页面 | 至多证明某个字符串或配置已公开 | 待验证；暂不泛抓 JS bundle 或用文件 hash 变化发模型消息 |

以上是单次访问结果，不是可用率或稳定性测试。AI Tracker 的失败证据指向当时 Cloudflare 无法连接其 Tunnel，不足以判断长期停服。OpenRouter 文档示例要求 Bearer Token，而本次无认证请求成功；实施时应保留这一差异，不能保证接口会永久匿名开放。

### AI Tracker：覆盖有用，速度与取证有限

其官方说明称监测 220 多个来源，但 Feed 仅收录已发布消息，晚于 X 约 10 分钟。因此，5 分钟轮询 Feed 也不能承诺比它的 X 更早。当前公开开发者页未提供足以直接配置的 webhook 地址、鉴权和重放契约，首期使用有文档的 JSON Feed。[开发者说明](https://live.aitracker.bot/developers)

本地历史 Feed 的字段是 `id/url/title/content_text/date_published/tags`，`url` 指向 Tracker 的模型页面。目录还提供 `eventIds/postIds/stage/lastSeenAt`，但这些并非上游原始响应。下一步应针对候选消息补充原始来源链接及快照，不能把摘要当成独立取证。`model-news` 当前不可用，本次不承诺它具有哪些字段。

### 两个实测反例：目录不能替代可用性

1. models.dev 本次含 301 个 `deprecated` 条目，其中 OpenCode 有 44 个。`opencode/exo-free` 在该目录中标为 deprecated，但本次读取的 OpenCode 官方文档仍列为限时免费。只能记录来源冲突；没有实际调用，不能判定哪一边代表当前运行状态。[models.dev API](https://models.dev/api.json) · [OpenCode 文档](https://opencode.ai/v2/docs/console/models/)
2. OpenAI SDK 的 `ChatModel` 包含 `gpt-5.1-mini`，同文件明确说明它不是受支持的 ID，保留是为了 SDK 兼容。因此仅扫描类型枚举会产生假线索，必须一起阅读 diff、注释与废弃说明。[固定提交源码](https://github.com/openai/openai-python/blob/325da45934ddb1c1b6a0bed1681a8cf46c9a8712/src/openai/types/shared/chat_model.py)

Anthropic 的模型类型还允许任意 `str`，枚举不是完整的服务目录；枚举中没有某 ID 也不能作为不存在的证据。[固定提交源码](https://github.com/anthropics/anthropic-sdk-python/blob/f6346e63b7507a596e6e8acc9425cae2dfc7bf24/src/anthropic/types/model.py)

### Hugging Face 与 GitHub 的接入区别

Hugging Face 官方支持监听其他用户或组织的仓库 webhook；GitHub 仓库 webhook 则要求拥有者或管理员权限。对第三方 SDK 仓库应先使用按文件路径筛选的提交轮询，不能设计成向对方仓库安装 webhook。[HF webhooks](https://huggingface.co/docs/hub/en/webhooks) · [GitHub webhooks 权限](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks)

HF 首期采用公开 API 轮询，不新增接收服务。注意分页：本次 `limit=3` 只是接口抽样，不能用于宣称组织全量覆盖；正式采集需沿 Link 分页直到覆盖已存游标，不能漏掉一轮内的批量新增。`lastModified` 也不能直接当作首次公开或正式发布的时间。

## 4. 最小实施方式

流程保持为：**维护侧定时采集 → 保存变化前后证据 → 生成人可核对的事件 → 离线构建现有静态网站。** 访客只浏览，不需要 API key 或运行命令。模型复测由维护者在候选事件值得调查时触发。

建议先复用 `stealthprint/updates.py`、刷新脚本与现有页面，增加一个定时入口和对应的少量回归用例。没有必要先增加插件接口、消息队列、向量库、LLM 分类服务或数值置信度体系。

首期建议频率如下；这是起始配置建议，不是来源服务承诺：

| 采集对象 | 起始频率 | 差分范围 |
| --- | --- | --- |
| OpenRouter 目录 | 5 分钟 | ID、alias target、canonical slug、上下文、模态、价格及退役信息；不对实时 benchmark 波动发消息 |
| AI Tracker Feed | 5 分钟 | 新事件、同 ID 内容修订；故障按 Retry-After 退避 |
| AI Tracker 目录 | 15 分钟 | 模型归属与状态信息；失败不阻塞其他来源 |
| models.dev | 15 分钟 | 只关注指定 provider 的新增 ID 和状态变化；使用 ETag |
| HF DeepSeek 组织 | 15 分钟 | 新仓库和与模型发布相关的文件变化；先列表，再取候选详情 |
| 两个官方 SDK 文件 | 15 分钟 | 文件提交与语义差分；固定 commit SHA，排除纯排版变化 |

约为每天 1,056 次计划 GET，另加分页和候选详情请求；实际频率随退避调整。仅 models.dev 本次响应就约 5.83 MB，不宜反复无条件全量下载；本次 ETag/304 已验证。公开元数据查询不产生模型推理费用，但不能因此称整个系统零成本。[GitHub 条件请求建议](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

OpenRouter 本次响应的缓存头含 `max-age=120`、`stale-while-revalidate=3600` 和 `stale-if-error=3600`。轮询周期不是源数据新鲜度保证；归档时保存 Date、Age、Last-Modified 等响应头。

先用 GitHub Actions 可以减少新增基础设施，但 schedule 可能延迟或丢弃，公开仓库长期无活动也会停用。适合普通动态页，不能承诺分钟级告警 SLA。若后续验收证明需要更稳定的时效，再选择常驻调度；本次未部署任何定时任务。[GitHub schedule 限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

### 必须补齐的证据处理

- 每个来源单独保存上次成功时间与最近失败，避免一个源故障拖住全部刷新。304 代表成功检查且内容未变。
- 变化时保留原始响应、来源 URL、HTTP 状态、抓取时间和 SHA-256；修订同 ID 时保留前版，撤回与恢复也记录为后续事件。
- 比较目录前按 ID 排序并只选关注字段；首次运行仅建立基线。只在完整且成功的目录获取后判断条目消失，缺失先记为“当前未观察到”，不能自动认定下架。
- 分开保存来源所称的发布时间、本系统首次观察时间和 API 实测时间。已有旧数据没有首次观察记录时保留未知，不用迁移回填制造时间。
- 跨来源只根据明确 URL、官方映射或人工确认关联。相似名称、相近发布日期不能自动合并；models.dev 与 OpenCode 也不应被计作完全独立的双重确认。
- 页面同时展示消息的原文判断与本站结论。公开标识、官方发布、某入口可调用、身份测量是不同事实，可以并存，不能用一个状态字段相互覆盖。

这里的规则用于实现与人工审阅，不新增一套判断模型真假的硬编码评分器。

## 5. 与 stealthprint 复测如何衔接

新 ID 或匿名别名出现后，先确认它对应的公开入口、使用条件和调查价值，再安排小规模实验。复测复用现有 `--record`、tokenizer 与 wrapper，记录实验入口、模型 ID、时间、候选词表版本和失败样本。

相同 tokenizer 差分只支持计数路径相近，不能证明权重或厂商相同。目录里的上下文、价格和模态是来源声明；能力与运行状态要由独立实验支持。大上下文、视觉或更多对照只针对尚未回答的问题追加，不在每次目录变化时自动跑全套。

本次没有调用任何模型推理接口，没有使用模型密钥，也没有测量新模型身份。

## 6. 回放、试运行与最终取舍

现有历史快照提供了一个可复现的回放样本：`deepseek-v4.1-flash-1017` 在 2026-10-17 11:18:24 北京时间被 Tracker 标为 leak，16:07:00 被标为 release，两条消息相隔约 4 小时 49 分。它证明本地应保留两条来源事件；不能据此推算我们获得了同样提前量，也不能确认上游精确切换时间。

实施后的必要验收：

| 场景 | 通过条件 |
| --- | --- |
| 泄露后发布 | 两条事件与各自日期保留；发布时间不覆盖首次观察时间 |
| 同 ID 改稿或撤回 | 前后内容可读，并标明修订时间 |
| 530、429 或解析失败 | 上次有效数据保留，来源显示故障，其他来源继续刷新 |
| 304 或相同内容 | 更新成功检查时间，不新增模型事件 |
| 首次目录与重新排序 | 建立基线或无变化，不批量制造新模型消息 |
| 分页不全或目录暂时缺项 | 不输出整批“下架”结论 |
| 来源相互矛盾 | 并列展示原始依据，标明待核对 |

试运行统计需分别报告：来源可用情况、有效变化的人工确认比例、重复率、复核耗时、与对照来源的时间差。建议以“至少 20 条变化且 80% 以上无需撤销、每日复核不超过 10 分钟”作为继续扩源的初始讨论标准；样本不足时延长观察，不把它包装成已验收指标。全网泄露总量不可知，不报告没有分母的覆盖率。

最终取舍：

- **采用**：现有静态站、Python 采集、少数公开目录与官方仓库、变化证据归档、维护者按需复测。
- **采用但有边界**：AI Tracker 作聚合补充，models.dev 作社区元数据；均不单独证明模型身份或实际可调用。
- **暂不采用**：自动从字符串猜厂商、全量前端脚本爬取、自动高成本探针、为少数 JSON 来源单独部署监控平台。
- **待验证**：7 天时效与误报表现、AI Tracker 恢复情况、增加某个具体前端来源能否带来独有证据。

若后续只有少数网页需要变化监测，可以评估 [changedetection.io 的 JSONPath/jq 和页面差分能力](https://github.com/dgtlmoon/changedetection.io/blob/master/README.md#json-api-monitoring)；当前已有采集链路，不把它作为首期依赖。

## 证据归档

本次调研的原始目录、SDK 文件、HTTP 530 错误体、哈希清单及 Release 回执保存在本地原工作区的 `work/model-leak-monitoring-20261020/`，并未作为代码文件发布。实时监测的后续证据由独立 `monitor-data` 分支持久保存，通过站点下载；具体上线与验收状态见 [上线设计](model-monitoring-design.zh-CN.md)。
