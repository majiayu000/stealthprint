# Issue #9 后续实验

2026-09-29。本轮完成公开 tokenizer 的本地模板对照；没有调用推理 API。

## 结论与 Issue 状态

建议 #9 保持开放，明确标记部分完成。#10 已合并词表排除；本轮补充了六个模型的模板实测，但 Meta 官方 3.1/3.3 tokenizer 下载返回 401，受控推理实验也尚未执行。Pareto 的产品身份揭晓不能替代具体底座或训练来源的验证。

模板对照有助于检查提示格式，却不能单独识别模型。六个候选在相同显式系统提示下的额外 token 数全部为 **15**。Hermes 4 的 70B/405B 在本轮所有成功测量中完全相同。历史 API 的 +16/17 是计费计数减去原文 token 数，与本地真实序列化不是同一个可观测量，不能直接据此排除这些底座。

## 可复现记录

- 输入与模型 revision：[issue9-template-inputs.json](issue9-template-inputs.json)。只下载公开 tokenizer/template 文件，不加载权重，不执行远程代码。
- 原始结果：[issue9-template-results.json](issue9-template-results.json)。包含每份文件 SHA-256、精确消息、渲染结果、token 数和模板异常。
- 脚本：[issue9_templates.py](../scripts/issue9_templates.py)。在仓库根目录执行 `.venv/bin/python scripts/issue9_templates.py`。
- 环境：`transformers==4.56.2`、`tokenizers==0.22.2`、`jinja2==3.1.6`。原始文本计数关闭 special tokens；`apply_chat_template` 开启 generation prompt；固定模板日期为 `26 Jul 2024`。
- 五种输入：归档 base、base 加中文探针、去掉 base 首尾空白、显式系统提示、多轮消息。显式系统提示为 `You are a helpful assistant.`；历史回复为 `Hello.`。
- 对每个成功样本验证 `apply_chat_template(tokenize=True)` 与渲染后 `encode(add_special_tokens=False)` 的 token ID 序列相同，并额外测量关闭 generation prompt 的差值。

空环境可先运行 `uv venv .venv`，再运行 `uv pip install --python .venv/bin/python transformers==4.56.2 tokenizers==0.22.2 jinja2==3.1.6`。本轮两次完整执行得到完全相同的结果文件 SHA-256：`37add2b80c33f7c7d6b03cd826e9a1a47f23f01e96708c9970a63900c9f3fe46`。

表中开销 = 完整模板 token 数 − 每条输入消息正文的裸 token 数之和。默认系统提示属于开销；显式传入的系统提示属于原文。

| 发布模型 | 默认单用户 base | 显式 system + base | 默认两轮 | 本地思考模板开关 |
|---|---:|---:|---:|---|
| Cogito v1 preview Llama 70B | 10 | 15 | 20 | `enable_thinking=True` 时分别为 20 / 20 / 30 |
| Hermes 4 70B | 24 | 15 | 34 | `thinking=True` 时单用户为 82，显式 system 仍为 15 |
| Hermes 4 405B | 24 | 15 | 34 | 与 70B 相同 |
| Llama-3.1-Nemotron-Ultra-253B-v1 | 19 | 15 | 29 | 本轮只测默认模板；推理 on/off 尚未调用模型 |
| Arcee Llama-3.1-SuperNova-Lite | 10 | 15 | 20 | 本轮只测默认模板 |
| AI2 Llama-3.1-Tulu-3-70B | 10 | 15 | 22 | 本轮只测默认模板 |

全部 55 个模板/输入组合中，53 个返回有效计数，2 个返回明确的模板异常。异常均为 Hermes 4 的 `thinking=True` 处理不含 `</think>` 的历史 assistant 消息时抛出 `UndefinedError: list object has no element 1`。这是固定 revision 上观察到的模板行为，不能记作成功计数。显式设置官方支持的 `keep_cots=True` 后，两种尺寸的同一历史输入均正常渲染，开销为 92；原始异常也保留在结果中。

Hermes 4 的显式 system 消息会替代默认思考提示，因此 `thinking=True` 不保证把思考指令加入现有 system 消息。实时测试必须发送实际官方触发提示，不能仅假定客户端开关已经生效。

Meta 官方 `meta-llama/Llama-3.1-8B-Instruct` 和 `meta-llama/Llama-3.3-70B-Instruct` 的 `tokenizer_config.json` 在无认证下载时返回 401。本轮没有使用衍生模型或镜像冒充这两个官方实测锚点。

## 推理实验尚缺什么

本轮公开查询 OpenRouter `/api/v1/models`，目录仍包含 `unbiased/pareto` 和 `nousresearch/hermes-4-405b`。目录存在只说明被列出，不能证明推理请求可用。当前进程和已检查的项目配置没有 OpenRouter 凭据；发现的 OpenCode 登录不会用于其他提供商。没有发出付费推理请求，也没有据此推断模型行为。

取得可用凭据后的最小受控实验如下。

1. 对 Pareto 重新测量归档的中文、法语、数字、emoji 差分，记录时间、路由/provider 和计数分布。当前入口结果单独归档，不当作 9 月 17 日原模型的历史补测。
2. 同一任务交错测试四个 system 条件：中性基线、普通的认真作答提示、Cogito 官方触发提示、Hermes 4 官方触发提示。后两者逐字取自固定 revision 的发布模板；普通提示用于区分一般遵循指令与特定触发机制。
3. 使用英文复述、中文复述和简单可验证数学题，每组重复三次。Pareto 为目标；目录中的 Hermes 4 405B 作为其触发提示的正对照。Cogito v1 70B 不在本次返回的目录中，因此没有 Cogito 正对照前不能声称它的触发机制已被确认或否定。
4. 固定采样参数与输出预算，记录所有请求的实际 provider、时间、usage、结束原因、最终输出、公开 reasoning 字段是否存在及其长度。超时、错误和 `finish_reason=length` 单独列出，不能把截断或空输出当作不开启思考。
5. 优先使用服务公开的 reasoning token 计数。`completion_tokens` 减去可见文本的本地计数仅称为计数差额，因为 EOS、网关重计和词表差异也能造成差额；不能直接把它当作隐藏思考量。
6. 即使 Pareto 对某个触发提示响应更明显，也只能报告这个入口的行为相关性，不能据此认定模型来自 Cogito 或 Hermes。

官方触发机制来源：[Cogito](https://huggingface.co/deepcogito/cogito-v1-preview-llama-70B)、[Hermes 4](https://huggingface.co/NousResearch/Hermes-4-70B)、[Nemotron](https://huggingface.co/nvidia/Llama-3_1-Nemotron-Ultra-253B-v1)。实际下载文件的不可变 revision 与哈希记录在输入和结果中。

## 后续处置边界

可以把本轮结果作为 #9 的进展证据。待官方锚点及受控行为实验补齐后，再按实测结论关闭；若维护者决定不继续研究，也应明确以停止研究的原因关闭，不能声称三项建议全部完成。本轮没有发布评论或改变 Issue 状态。
