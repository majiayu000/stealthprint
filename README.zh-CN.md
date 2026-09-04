# stealthprint

[English](README.md) · [中文](README.zh-CN.md)

对任何 OpenAI 兼容端点上的 stealth / 匿名模型做指纹分析的可复用库。库本身**不绑定任何模型名**——端点、模型 ID、密钥全部显式传入，换一个新模型改两个参数即可复跑同一套流程。

## 安装

```bash
pip install "stealthprint @ git+https://github.com/majiayu000/stealthprint.git"
# 带指纹层依赖：
pip install -e "git+https://github.com/majiayu000/stealthprint.git#egg=stealthprint[all]"
```

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
```

也可以每条命令显式传参：`stealthprint --base-url ... --model ... --api-key ... tokenizer`。全局 flag 放在子命令前后都可以。加 `--json` 可同时输出机器可读结果。

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

内置探针集（`stealthprint/data/probes.json`）覆盖 12+ 语言：英文 pangram、中、日、韩、泰、俄、法、代码缩进、emoji ZWJ/国旗、生僻 Unicode、数字/浮点、标点。差分 `Δ = T(base+probe) − T(base)` 会消掉聊天模板常数，剩下的就是模型自己词表的计数。

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
| L3 上下文 | `context` | 验证过的上下文上限（二分）+ 长上下文召回保真度（头/中/尾埋针） |
| L4 错误信封 | `errors` | 服务栈指纹：数字码 vs 字符串码、serde/Java 报错原文、接受哪些 role、校验风格 |
| L6 视觉 | `vision` | 视觉是不是真的？颜色真值 + token 开销随图像尺寸的斜率（恒定 => 占位符；随尺寸增长 => 真编码器）。`--repeats N` 测混挂率 |
| L6 视频 | `video` | 是否接受原生视频（`video_url`）vs 未知 `type: video` vs 把 mp4 当图 |
| L7 目录 | `catalog` | 同网关 A/B：wrapper 差、特殊 token 差分、`reasoning_effort=none`、一张图，对照具名兄弟（`--peers` / `--family`） |

刻意**不实现**：自称探针（「你是谁」）、注入、审查探针、emoji 密度——对 stealth 模型来说自我介绍经常是诱饵。原因见案例报告。

## 拉取对照词表

```bash
./fetch_tokenizers.sh            # 下载开源 tokenizer.json 候选到 tok/
```

候选就是目录里的文件——把任何 `tokenizer.json` 丢进去即可新增嫌疑词表（如 `tok/mynewmodel.json`）。tiktoken 的 `o200k_base` 自动包含。

## 案例报告

完整案例（GLM-5 词表判定、~1M 上下文、Flash 级图+视频、相对具名 `glm-5.3-flash` 的 +24 隐身 wrapper）：
[docs/case-omen-alpha.zh-CN.md](docs/case-omen-alpha.zh-CN.md) ·
[English](docs/case-omen-alpha.md)

## 前人工作

方法延续自 `iSimplifyMe/tokenizer-fingerprint`、`LuD1161/ox-alpha-identification-public`、`unclecode/modelprint`。stealthprint 新增：异构后端检测、视觉真值协议、wrapper 常数验证法、视频模态探针、同网关目录对照、多语言探针集。

## License

MIT
