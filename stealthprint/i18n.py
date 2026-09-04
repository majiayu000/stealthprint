import json
import os

_LANG = os.environ.get("STEALTHPRINT_LANG", "en")

CATALOGS = {
    "en": {
        "cli.desc": "stealthprint — fingerprint an OpenAI-compatible model (tokenizer, context, errors, vision, catalog A/B)",
        "cli.tok": "L1 tokenizer differential vs local tokenizer.json files",
        "cli.wrap": "L2 template/wrapper overhead constant",
        "cli.ctx": "L3 context limit (binary search) + needle retrieval",
        "cli.err": "L4 error envelope family (malformed parameters)",
        "cli.vision": "L6 vision ground truth (color/size probes)",
        "cli.video": "L6 video modality probe (tiny mp4 content-block shapes)",
        "cli.cat": "L7 same-gateway catalog A/B (wrapper offset, vision, effort=none)",
        "err.no_key": "error: no API key (pass --api-key or set STEALTHPRINT_API_KEY)",
        "err.no_base": "error: no base URL (pass --base-url or set STEALTHPRINT_BASE_URL)",
        "err.no_model": "error: no model id (pass --model or set STEALTHPRINT_MODEL)",
        "err.missing_dep": "error: missing dependency '{dep}'. Install: pip install {pkgs}",
        "tok.querying": "== querying target ({n} probes) ==",
        "tok.failed": "  {name}: FAILED {err}",
        "tok.skip": "skip {name}: {err}",
        "tok.table": "%-14s | candidates | API",
        "tok.ranking": "== ranking (exact matches / MAE) ==",
        "tok.match": "  <== MATCH",
        "tok.wrapper": "== wrapper constant (api - raw, per candidate) ==",
        "wrap.header": "== wrapper constant across lengths ==",
        "wrap.hint": "constant api-vs-local delta across lengths => fixed chat template",
        "ctx.header": "== context limit binary search (bounded by gateway body limit) ==",
        "ctx.max": "max verified: {chars} chars => {tokens} prompt_tokens",
        "ctx.needle": "== needle-in-haystack (head/mid/tail) ==",
        "ctx.needle_fail": "needle FAILED: {err}",
        "ctx.expected": "expected: {codes}",
        "errf.header": "== error envelope family ==",
        "vision.header": "== vision ground-truth probes ==",
        "vision.control": "no-image control prompt_tokens={pt}",
        "vision.hint": "compare (+delta) across sizes: size-dependent => real encoder; constant => placeholder",
        "vision.repeat": "== vision repeats ({n}x {size} solid color) ==",
        "vision.repeat_counts": "counts: {counts}",
        "video.header": "== video content-block shapes ==",
        "cat.header": "== same-gateway catalog ({n} models) ==",
        "cat.ids": "ids: {ids}",
        "cat.peers": "comparing: {peers}",
    },
    "zh": {
        "cli.desc": "stealthprint —— 对任意 OpenAI 兼容模型做指纹分析（分词器 / 上下文 / 错误 / 视觉 / 目录对照）",
        "cli.tok": "L1 分词器差分（对照本地 tokenizer.json 词表）",
        "cli.wrap": "L2 模板 / wrapper 常数开销",
        "cli.ctx": "L3 上下文上限（二分）+ 针找回测试",
        "cli.err": "L4 错误信封家族（畸形参数）",
        "cli.vision": "L6 视觉真值测试（颜色 / 尺寸探针）",
        "cli.video": "L6 视频模态探针（小 mp4 的多种 content 形状）",
        "cli.cat": "L7 同网关目录对照（wrapper 差、视觉、effort=none）",
        "err.no_key": "错误：缺少 API key（用 --api-key 或设置 STEALTHPRINT_API_KEY）",
        "err.no_base": "错误：缺少 base URL（用 --base-url 或设置 STEALTHPRINT_BASE_URL）",
        "err.no_model": "错误：缺少模型 ID（用 --model 或设置 STEALTHPRINT_MODEL）",
        "err.missing_dep": "错误：缺少依赖 '{dep}'。安装：pip install {pkgs}",
        "tok.querying": "== 正在探测目标（{n} 条探针）==",
        "tok.failed": "  {name}：失败 {err}",
        "tok.skip": "跳过 {name}：{err}",
        "tok.table": "%-14s | 候选词表 | API",
        "tok.ranking": "== 排名（精确匹配数 / MAE）==",
        "tok.match": "  <== 匹配",
        "tok.wrapper": "== wrapper 常数（API 减本地原始计数）==",
        "wrap.header": "== 各长度下的 wrapper 常数 ==",
        "wrap.hint": "API 与本地差值恒定 => 固定聊天模板",
        "ctx.header": "== 上下文上限二分（受网关 body 限制封顶）==",
        "ctx.max": "已验证上限：{chars} 字符 => {tokens} prompt_tokens",
        "ctx.needle": "== 针测试（头 / 中 / 尾）==",
        "ctx.needle_fail": "针测试失败：{err}",
        "ctx.expected": "预期答案：{codes}",
        "errf.header": "== 错误信封家族 ==",
        "vision.header": "== 视觉真值探针 ==",
        "vision.control": "无图对照 prompt_tokens={pt}",
        "vision.hint": "比较不同尺寸的 (+delta)：随尺寸增长 => 真编码器；恒定 => 占位符",
        "vision.repeat": "== 视觉重复探针（{n} 次 {size} 纯色）==",
        "vision.repeat_counts": "计数：{counts}",
        "video.header": "== 视频 content 形状探针 ==",
        "cat.header": "== 同网关目录（{n} 个模型）==",
        "cat.ids": "ids: {ids}",
        "cat.peers": "对照：{peers}",
    },
}

VALID_LANGS = sorted(CATALOGS)


def set_lang(lang):
    global _LANG
    if lang in CATALOGS:
        _LANG = lang


def get_lang():
    return _LANG


def t(key, **kw):
    template = CATALOGS.get(_LANG, CATALOGS["en"]).get(key) or CATALOGS["en"].get(key) or key
    return template.format(**kw) if kw else template


def load_probes(path=None):
    """Load a probe set: {base: str, probes: [[name, text], ...]}.

    Defaults to the bundled multilingual probe set (24 probes, 12+ languages).
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "data", "probes.json")
    data = json.load(open(path, encoding="utf-8"))
    if "base" not in data or "probes" not in data:
        raise ValueError("probe file must contain 'base' and 'probes'")
    return data
