import json
import os

_LANG = os.environ.get("STEALTHPRINT_LANG", "en")

CATALOGS = {
    "en": {
        "cli.desc": "stealthprint — fingerprint an OpenAI-compatible model (tokenizer, context, errors, vision, catalog A/B)",
        "cli.tok": "L1 tokenizer differential vs local tokenizer.json files",
        "cli.wrap": "L2 template/wrapper overhead constant",
        "cli.ctx": "L3 successful input sizes + needle retrieval",
        "cli.err": "L4 error envelope family (malformed parameters)",
        "cli.vision": "L6 vision ground truth (color/size probes)",
        "cli.video": "L6 video modality probe (tiny mp4 content-block shapes)",
        "cli.cat": "L7 same-gateway catalog A/B (wrapper offset, vision, effort=none)",
        "cli.echo": "L1b completion-side vocab verification (model echoes probes)",
        "cli.tools": "L5 tool-calling probes (schema overhead, tool_choice, real calls)",
        "cli.survey": "gateway census: classify catalog reachability, then L1-probe the reachable models",
        "survey.resume": "resuming: {n} models already in {path}, skipped",
        "survey.model_ok": "  {model} ({mode}) base={base} best={best} exact={exact}/{n} mae={mae}",
        "survey.model_class": "  {model} -> {cls} ({detail})",
        "survey.summary": "== summary: {catalog} cataloged, {measured} measured, {paid} paid-walled ==",
        "echo.header": "== completion-side vocab check (echo probes) ==",
        "tools.header": "== tool-calling behavior ==",
        "wrap.turns": "== per-turn template accumulation ==",
        "xrep.mixed": "Counts vary or samples failed; retaining observations without a ranking",
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
        "wrap.hint": "Raw API counts only; a wrapper residual requires an explicit local tokenizer",
        "ctx.header": "== successful input lengths (stop on first error) ==",
        "ctx.max": "max verified: {chars} chars => {tokens} prompt_tokens",
        "ctx.failed": "context search FAILED: {err}",
        "ctx.needle": "== needle-in-haystack (head/mid/tail) ==",
        "ctx.needle_fail": "needle FAILED: {err}",
        "ctx.expected": "expected: {codes}",
        "errf.header": "== error envelope family ==",
        "vision.header": "== vision ground-truth probes ==",
        "vision.control": "no-image control prompt_tokens={pt}",
        "vision.hint": "Compare color answers and size-dependent counts; counts alone do not identify an encoder",
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
        "cli.ctx": "L3 成功输入长度 + 针找回测试",
        "cli.err": "L4 错误信封家族（畸形参数）",
        "cli.vision": "L6 视觉真值测试（颜色 / 尺寸探针）",
        "cli.video": "L6 视频模态探针（小 mp4 的多种 content 形状）",
        "cli.cat": "L7 同网关目录对照（wrapper 差、视觉、effort=none）",
        "cli.echo": "L1b 生成侧词表验证（模型复述探针文本）",
        "cli.tools": "L5 工具调用探针（schema 开销、tool_choice、真实调用）",
        "cli.survey": "网关普查：目录可达性分类，再对可达模型跑 L1 探针",
        "survey.resume": "断点续跑：{path} 已有 {n} 个模型，跳过",
        "survey.model_ok": "  {model}（{mode}）base={base} best={best} exact={exact}/{n} mae={mae}",
        "survey.model_class": "  {model} -> {cls}（{detail}）",
        "survey.summary": "== 汇总：目录 {catalog} 个，实测 {measured} 个，付费墙 {paid} 个 ==",
        "echo.header": "== 生成侧词表校验（复述探针）==",
        "tools.header": "== 工具调用行为 ==",
        "wrap.turns": "== 每轮模板累积 ==",
        "xrep.mixed": "计数变化或样本失败：保留观测，不生成身份排名",
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
        "wrap.hint": "这里只采集 API 计数；模板差额需明确本地词表参照",
        "ctx.header": "== 成功输入长度阶梯（遇错停止并保留成功点）==",
        "ctx.max": "已验证上限：{chars} 字符 => {tokens} prompt_tokens",
        "ctx.failed": "上下文搜索失败：{err}",
        "ctx.needle": "== 针测试（头 / 中 / 尾）==",
        "ctx.needle_fail": "针测试失败：{err}",
        "ctx.expected": "预期答案：{codes}",
        "errf.header": "== 错误信封家族 ==",
        "vision.header": "== 视觉真值探针 ==",
        "vision.control": "无图对照 prompt_tokens={pt}",
        "vision.hint": "核对颜色答案与尺寸计数；固定开销或增长曲线均不能单独确认编码器",
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
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    if "base" not in data or "probes" not in data:
        raise ValueError("probe file must contain 'base' and 'probes'")
    names = [item[0] for item in data["probes"]]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("probe names must be nonempty strings")
    if len(names) != len(set(names)) or "base" in names or not names:
        raise ValueError("probe names must be unique, nonempty, and cannot use reserved name 'base'")
    return data
