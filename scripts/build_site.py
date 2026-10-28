"""Build the static investigation archive from repository reports and JSON.

Install with pip install -e '.[site]', then run python scripts/build_site.py.
Generated pages and byte-for-byte measurement copies go into site/dist/.
"""

from __future__ import annotations

import hashlib
from html import escape, unescape
import json
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from string import Template
from urllib.parse import urlsplit, urlunsplit
from markdown.extensions.toc import slugify_unicode

import markdown
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stealthprint.updates import SOURCES, candidate_ids, group_updates, load_state, parse_time


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
OUT = SITE / "dist"
MONITOR_DIR = ROOT / ".monitor-data"
REPO = "https://github.com/majiayu000/stealthprint/blob/main/"
BUILD = {}
CASES = json.loads((SITE / "cases.json").read_text(encoding="utf-8"))
TEMPLATE = Template((SITE / "template.html").read_text(encoding="utf-8"))
DOC_PAGES = {
    f"docs/case-{case['slug']}.zh-CN.md": f"cases/{case['slug']}/index.html"
    for case in CASES
}
DOC_PAGES["docs/issue9-followup.md"] = "research/issue9/index.html"


def read_json(filename):
    return json.loads((ROOT / "docs" / filename).read_text(encoding="utf-8"))


def relative(page, target):
    return posixpath.relpath(target, posixpath.dirname(page) or ".")


def status(case):
    return f'<span class="status status-{escape(case["tone"])}">{escape(case["status"])}</span>'


def render_page(page, title, description, content, active="home", body_class=""):
    values = {
        "title": escape(f"{title} · Stealthprint"),
        "description": escape(description, quote=True),
        "main": content,
        "body_class": escape(body_class),
        "home": relative(page, "index.html"),
        "guide": relative(page, "guide/index.html"),
        "methods": relative(page, "methods/index.html"),
        "updates": relative(page, "updates/index.html"),
        "research": relative(page, "research/issue9/index.html"),
        "css": relative(page, "assets/styles.css") + "?v=" + hashlib.sha256((SITE / "styles.css").read_bytes()).hexdigest()[:12],
        "js": relative(page, "assets/site.js") + "?v=" + hashlib.sha256((SITE / "site.js").read_bytes()).hexdigest()[:12],
        "build_info": relative(page, "assets/build.json"),
        "source_version": escape(BUILD.get("source_digest", "")[:12]),
    }
    for name in ("home", "guide", "methods", "updates"):
        values[f"active_{name}"] = 'aria-current="page"' if active == name or (name == "guide" and active == "research") else ""
    target = OUT / page
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.substitute(values), encoding="utf-8")


def document_link(href, source, page):
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path:
        return href
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source), parts.path))
    if path in DOC_PAGES:
        target = relative(page, DOC_PAGES[path])
    elif path.startswith("docs/") and path.endswith(".json"):
        target = relative(page, f"data/{posixpath.basename(path)}")
    else:
        target = REPO + path
    return urlunsplit(("", "", target, parts.query, parts.fragment))


def report(source, page):
    parser = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3", "slugify": slugify_unicode}},
    )
    rendered = parser.convert((ROOT / source).read_text(encoding="utf-8"))
    rendered = re.sub(
        r'href="([^"]+)"',
        lambda match: 'href="' + escape(document_link(unescape(match.group(1)), source, page), quote=True) + '"',
        rendered,
    )
    rendered = re.sub(r"(<table\b[^>]*>.*?</table>)", r'<div class="table-scroll">\1</div>', rendered, flags=re.S)
    rendered = re.sub(r"<(\/?)[hH]([1-5])(\b[^>]*)>", lambda m: f"<{m[1]}h{int(m[2]) + 1}{m[3]}>", rendered)
    return f'<div class="article-layout"><article class="prose">{rendered}</article><aside class="article-toc" aria-label="报告目录"><strong>报告目录</strong>{parser.toc}</aside></div>'


def table(headers, rows, css="comparison-table"):
    heading = "".join(f'<th scope="col">{escape(str(value))}</th>' for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f'<table class="{css}"><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>'


def ranks_table(ranks, probe_data=None):
    rows = []
    for name, exact, total, mae in sorted(ranks, key=lambda row: (-row[1], row[3], row[0])):
        if probe_data is not None:
            label = f'<button type="button" data-candidate="{escape(name, quote=True)}" aria-pressed="false" aria-controls="probe-details">{escape(name)}</button>'
        else:
            label = f'<span class="mono">{escape(name)}</span>'
        full = " exact" if exact == total else ""
        rows.append(f'<tr><td>{label}</td><td><div class="match-cell"><span class="match-track" aria-hidden="true"><span class="match-fill{full}" style="width:{exact / total * 100:.3f}%"></span></span><span class="match-number">{exact} / {total}</span></div></td><td>{mae:.3f}</td></tr>')
    content = '<table class="comparison-table rank-table"><thead><tr><th scope="col">候选词表</th><th scope="col">精确匹配</th><th scope="col">MAE</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
    if probe_data is not None:
        payload = json.dumps(probe_data, ensure_ascii=False).replace("<", "\\u003c")
        content += f'''<noscript><p>逐条探针交互需要 JavaScript；<a href="#data">下载归档 JSON 查看完整明细</a>。</p></noscript><details id="probe-details" class="probe-details">
          <summary>逐条探针对照</summary>
          <p id="probe-selection" class="probe-selection" aria-live="polite">选择上方候选词表，查看每条探针的差额。</p>
          <div class="table-scroll"><table class="comparison-table"><thead><tr><th scope="col">探针</th><th scope="col">目标增量</th><th scope="col">候选计数</th><th scope="col">差额</th></tr></thead><tbody id="probe-rows"></tbody></table></div>
        </details><script type="application/json" id="probe-data">{payload}</script>'''
    return content


def evidence(case):
    slug = case["slug"]
    data = read_json(f"case-{slug}-measurements.json")
    if slug == "pixel-canary":
        candidates = dict(data["analysis"]["candidate_ranking"])
        extra = read_json("case-pixel-canary-supplement-measurements.json")
        candidates.update(extra["additional_local_comparisons"])
        ranks = [(name, row["exact"], row["total"], row["mae"]) for name, row in candidates.items()]
        probes = {
            name: [{"probe": row["probe"], "api": row["api_delta"], "local": row["api_delta"] - row["residual"]} for row in item["details"]]
            for name, item in candidates.items()
        }
        return '<h2>21 套词表，逐一对照</h2><p class="section-intro">比较同一组 24 条有效探针。没有候选完整匹配；条形长度表示匹配数，不是身份概率。点击候选名称可展开逐条差额。</p>' + ranks_table(ranks, probes) + '<p class="data-caption">来源：主实验 analysis.candidate_ranking + 追加补测 additional_local_comparisons。MAE 为 token 增量的平均绝对误差；数值越小仅表示本组计数差异较小。</p>'
    if slug == "omen-alpha":
        first = data["tokenizer_round1_16_probes"]
        observed = first["api_delta"]
        ranks, probes = [], {}
        for name, local in first["local_T_probe"].items():
            comparison = [{"probe": probe, "api": value, "local": count} for (probe, value), count in zip(observed.items(), local)]
            ranks.append((name, sum(row["api"] == row["local"] for row in comparison), len(comparison), sum(abs(row["api"] - row["local"]) for row in comparison) / len(comparison)))
            probes[name] = comparison
        second = data["tokenizer_round2_8_probes"]
        second_rows = [(key, value, glm, o200k) for (key, value), glm, o200k in zip(second["api_delta"].items(), second["glm5_delta"], second["o200k_delta"])]
        return '<h2>首轮 16 条词表探针</h2><p class="section-intro">GLM 两套公开词表在首轮均为 16/16。这里保留首轮分母；后续八条独立加测列在下方，避免混用候选的比较范围。</p>' + ranks_table(ranks, probes) + '<p class="data-caption">来源：tokenizer_round1_16_probes.api_delta / local_T_probe。MAE 由本轮归档计数计算。</p><details class="vector-details"><summary>查看第二轮 8 条加测</summary><div class="table-scroll">' + table(["探针", "目标增量", "GLM-5", "o200k"], second_rows) + '</div></details>'
    if slug == "space-bunny":
        layer = data["l1_tokenizer_differential"]
        ranks = []
        for name, text in layer["local_ranking_vs_space_bunny"].items():
            match = re.search(r"(\d+)/(\d+).*?MAE\s+([\d.]+)", text)
            if match is None:
                raise ValueError(f"Cannot read archived ranking: {name}: {text}")
            ranks.append((name, int(match[1]), int(match[2]), float(match[3])))
        target = layer["api_delta"]["space-bunny-free"]
        peers = layer["api_delta_minimax"]
        kimi = layer["api_delta"]["kimi-k3"]
        peer_rows = [(key, value, peers["minimax-m3"][key], peers["minimax-m2.5"][key], kimi[key]) for key, value in target.items()]
        return '<h2>词表线索指向 MiniMax 家族</h2><p class="section-intro">本地候选比较了 24 条探针。MiniMax 的全部计数吻合，具名 m3 与 m2.5 也共享签名，因此单靠词表无法选择具体代际。</p>' + ranks_table(ranks) + '<p class="data-caption">来源：l1_tokenizer_differential.local_ranking_vs_space_bunny，保留归档匹配数与 MAE。</p><details class="vector-details"><summary>查看同网关 24 条逐项对照</summary><div class="table-scroll">' + table(["探针", "匿名入口", "minimax-m3", "minimax-m2.5", "kimi-k3"], peer_rows) + '</div></details>'
    if slug != "union-alpha":
        raise ValueError(f"No evidence renderer for case {slug}")
    distribution = data["l1_tokenizer"]["path_distribution_base_text"]
    total = distribution["n_total"]
    distribution_label = f"{total} 次相同输入：" + "，".join(f"{tokens} token {count} 次" for tokens, count in distribution.items() if tokens != "n_total")
    bars = "".join(f'<div class="distribution-row"><span class="distribution-label">{escape(tokens)} token</span><span class="distribution-track" aria-hidden="true"><span class="distribution-fill" style="width:{count / total * 100:.3f}%"></span></span><span class="distribution-count">{count} / {total}</span></div>' for tokens, count in distribution.items() if tokens != "n_total")
    successor = data["identity_reveal"]["successor_fingerprint"]["delta_pairs"]
    return f'<h2>同一输入，三种计数</h2><p class="section-intro">归档中的 24 次相同基线调用返回了三种输入计数。图展示计数分布；计数通道数量本身不能说明存在多少个模型。</p><div class="distribution" role="img" aria-label="{escape(distribution_label, quote=True)}">{bars}</div><p class="data-caption">来源：l1_tokenizer.path_distribution_base_text。各路径的计数和占比还曾随时间变化。</p><h3 class="subsection-heading">继任入口保留的四条签名</h3><p class="section-intro">2026-09-18 的 unbiased/pareto 入口实测。归档调查记录其与历史 B 通道一致；产品身份的官方揭晓和底层来源的推断分开保留。</p>' + table(["探针", "继任入口输入增量"], list(successor.items()))


def data_files(files, page):
    rows = []
    for filename, label in files:
        raw = (ROOT / "docs" / filename).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        href = escape(relative(page, "data/" + filename), quote=True)
        rows.append(f'<div class="data-file"><div><h3>{escape(label)}</h3><p class="data-filename">{escape(filename)} · {len(raw):,} bytes</p><p class="data-hash">SHA-256 / {digest}</p></div><div class="data-actions"><a href="{href}" target="_blank" rel="noopener">查看 JSON ↗</a><a href="{href}" download>下载</a></div></div>')
    return '<section id="data" class="data-section"><h2>归档数据</h2><p class="section-intro">文件保留归档原文，其中既有逐请求记录，也有从会话转录的测量摘要，完整度以各文件说明为准。SHA-256 验证文件字节未变，不代表独立验证了实验来源。</p>' + "".join(rows) + "</section>"


def interpretation(observation, inference):
    return f'<div class="evidence-reading"><div><h4>实测记录</h4><p>{escape(observation)}</p></div><div><h4>综合解读</h4><p>{escape(inference)}</p></div></div>'


def measurement_bars(points, unit, label):
    ceiling = max(value for _, value in points)
    rows = "".join(f'<div class="measurement-row"><span>{escape(name)}</span><span class="measurement-track" aria-hidden="true"><span style="width:{value / ceiling * 100:.3f}%"></span></span><span class="measurement-value">{value:,}</span></div>' for name, value in points)
    return f'<figure class="measurement-chart" aria-label="{escape(label, quote=True)}"><div class="measurement-axis"><span>{escape(label)}</span><span>{escape(unit)}</span></div>{rows}</figure>'


def focus_evidence(case):
    slug = case["slug"]
    data = read_json(f"case-{slug}-measurements.json")
    if slug == "omen-alpha":
        ab = data["same_gateway_ab_2026_09_04"]
        images = data["vision_repeats_64x64_red_2026_09_04"]
        videos = data["video_2026_09_04"]["video_url_data_uri"]
        target, flash, flagship = "omen-alpha", "glm-5.3-flash", "glm-5.3"
        red_target = images[target]["content"]["Red"] + images[target]["content"]["Red."]
        rows = [
            ["文本 hi / 输入 token", ab["hi_prompt_tokens"][target], ab["hi_prompt_tokens"][flash], ab["hi_prompt_tokens"][flagship]],
            ["64×64 红图 / HTTP 成功", f'{images[target]["http_200"]}/{images[target]["n"]}', f'{images[flash]["http_200"]}/{images[flash]["n"]}', f'{images[flagship]["n"]} 次均拒收图像'],
            ["红图 / 明确正确作答", f'{red_target}/{images[target]["n"]}', f'{images[flash]["content"]["Red"]}/{images[flash]["n"]}', "未取得作答"],
            ["红图 / 空输出", images[target]["content"]["empty"], images[flash]["content"]["empty"], "未取得作答"],
            ["红图 / 输入 token", images[target]["prompt_tokens"], images[flash]["prompt_tokens"], "无有效计数"],
            ["原生红视频 / 归档分类", f'HTTP {videos[target]["http"]} / {videos[target]["kind"]}', f'HTTP {videos[flash]["http"]} / {videos[flash]["kind"]}', f'HTTP {videos[flagship]["http"]}，拒收 video_url'],
            ["红视频 / 输入 token", videos[target]["prompt_tokens"], videos[flash]["prompt_tokens"], "无有效计数"],
            ["reasoning_effort=none", f'HTTP {ab["effort_none"][target]["http"]}', f'HTTP {ab["effort_none"][flash]["http"]} / [1210]', f'HTTP {ab["effort_none"][flagship]["http"]}'],
        ]
        content = '<div class="evidence-block"><h2>为什么进一步判断到 Flash 档？</h2><p class="section-intro">三个入口在同一 Go 网关上对照。表格同时保留响应、正确作答和空输出；每个入口的重复次数分别列出。</p><div class="table-scroll">' + table(["同一网关的对照", target, flash, flagship], rows) + '</div>'
        content += interpretation("匿名入口与具名 Flash 接受同一类图视频输入，文本、图像和视频计数分别多出 24 token；具名旗舰拒收图视频。", "词表与这些独立线索共同支持 Flash 档判断。参数处理差异仍说明服务路径不同，不能据此证明底层权重相同。")
        content += '<p class="data-caption">64×64 红图重复实验的输出预算为 48 token。8 次空输出单独保留，不能把 HTTP 200 的 24/24 写成识别正确率 24/24。视频摘要只保留了分类与计数；红色作答的描述同时来自当时的报告，不能从这份摘要独立还原完整响应。</p></div>'
        context = data["context_limit"]
        lower, upper = context["lower_scan"], context["upper_scan"]
        tokens = [lower["prompt_tokens"][0], lower["prompt_tokens"][-1], upper["prompt_tokens"][0], upper["prompt_tokens"][-2], upper["prompt_tokens"][-1]]
        points = [(f"成功点 {i + 1}", value) for i, value in enumerate(tokens)]
        content += '<div class="evidence-block"><h2>成功测到多长，在哪里受阻？</h2><p class="section-intro">下方节选五个成功测量点。最大的成功输入与网关限制分开记录，完整扫描在原始 JSON 中。</p>' + measurement_bars(points, "成功输入 / token", "归档上下文扫描节选")
        content += interpretation(f'最高成功输入为 {max(tokens):,} token。限制响应是 HTTP {context["body_limit_error"]["http"]}：{context["body_limit_error"]["message"]}', "这是已验证的输入下限和网关请求体限制；真实模型上下文上界未被触及。") + '<p class="data-caption">条形以本次最高成功值为标尺，不表示模型最大容量。</p></div>'
    elif slug == "space-bunny":
        layer = data["l1_tokenizer_differential"]
        target = layer["api_delta"]["space-bunny-free"]
        peers = layer["api_delta_minimax"]
        rows = [(name, f'{sum(target[key] == values[key] for key in target)}/{len(target)}', "同一 Go 网关") for name, values in peers.items()]
        content = '<div class="evidence-block"><h2>家族线索很强，代际为什么仍未确定？</h2><p class="section-intro">匿名入口的 24 条差分与两个具名 MiniMax 入口分别比较。共享签名让家族判断更有依据，也保留了代际歧义。</p>' + table(["具名入口", "与匿名入口一致的差分", "对照范围"], rows)
        content += interpretation("本地 MiniMax 词表 24/24 匹配，minimax-m3 和 minimax-m2.5 的实测差分也分别 24/24 一致。", "这些结果支持 MiniMax 家族线索。具体代际需要上下文和其他独立证据，词表无法唯一选择版本。") + '</div>'
        ladder = data["l3_context"]["ladder_local_tokens"]
        points = [(f'{row["local_t"]:,} 本地 token', row["pt"]) for row in ladder]
        content += '<div class="evidence-block"><h2>上下文阶梯与模板开销一起核对</h2><p class="section-intro">四档输入均成功。左侧为本地 MiniMax 计数，右侧为 API 返回输入计数。</p>' + measurement_bars(points, "API 输入 / token", "四档成功输入")
        content += '<div class="table-scroll">' + table(["本地输入 token", "API 输入 token", "差额", "归档结果"], [(row["local_t"], row["pt"], row["K"], "请求成功") for row in ladder]) + '</div>'
        outlier = data["l3_context"]["needle_810K"]
        content += interpretation("四档阶梯的计数差均为 +143，最大本地输入为 1,000,000 token。", "成功测量给出至少 1M 的下限；计数常数依赖本地词表这个参照，不能唯一确定模型身份。")
        content += f'<p class="data-caption">异常另存：810K 检索拼接样本为本地 {outlier["depth_local_t"]:,}、API {outlier["pt"]:,}，差额 +{outlier["pt"] - outlier["depth_local_t"]}。原记录将其归因为拼接边界效果，该归因仍作为解释保留。条形标尺仅反映本次最高实测。</p></div>'
        vision = data["l6_vision"]["space-bunny-free"]
        color_rows = [("64×64", "red", vision["64red_n4"]), ("64×64", "blue", vision["64blue_n4"]), ("1×1", "red", vision["1x1red_n4"])]
        content += '<div class="evidence-block"><h2>图像被接受后，实际回答了什么？</h2><div class="color-observations">'
        for size, color, answers in color_rows:
            color_name = "红色" if color == "red" else "蓝色"
            rgb = "255,0,0" if color == "red" else "0,0,255"
            content += f'<div class="color-observation"><span class="color-swatch" style="background:rgb({rgb})" aria-label="{color_name}测试输入"></span><div><h3>{size} 纯{color_name}输入</h3><p>{escape(" / ".join(answers))}</p></div></div>'
        content += '</div>' + interpretation(f'64×64 红蓝样本 8/8 明确答对，1×1 红色有一次回答 Black。文本控制计数为 {vision["text_ctrl_pt"]}，图像计数为 {vision["image_pt"]}，记录的尺寸开销恒为 +34。', "本组颜色实验提供图像理解与计数行为线索。固定开销本身不足以确定编码方式，更广泛的视觉任务与视频未由这组测试覆盖。") + '</div>'
    elif slug == "union-alpha":
        content = '<div class="evidence-block reveal-evidence"><h2>产品身份的揭晓来自哪里？</h2><p class="section-intro">2026-09-18，预览入口返回的 404 文案明确指向 Unbiased Pareto。这是归档的服务响应。</p><blockquote>This model was Unbiased\'s Pareto.</blockquote>'
        content += interpretation("官方入口的响应命名了产品；继任入口又保留了四条历史 B 通道计数签名。", "产品身份有直接来源。底层 checkpoint 和训练来源仍未确认；厂商名 Circuit & Chisel 仅来自模型自报，未独立核验。") + '</div><div class="evidence-block">' + evidence(case) + '</div>'
        tools = data["l5_tools"]
        content += '<div class="evidence-block"><h2>工具调用里留下了什么入口行为？</h2><p class="section-intro">归档记录三种 tool_choice 设置的结果一致。这里只展示调用声明和参数，未将它们描述为真实执行了天气或时间服务。</p>'
        content += table(["请求中的 tool_choice", "归档实际行为"], [(choice, "返回 2 次并行 tool_calls") for choice in ("none", "auto", "required")])
        content += interpretation(tools["parallel_calls"], "none 未按禁用工具的预期工作。工具语义是这条入口的行为线索，不能仅凭它归属底层模型。") + f'<p class="data-caption">原记录：{escape(tools["all_three_choices"])}。schema 额外开销为 {tools["schema_overhead_tokens"]} token。</p></div>'
        curve = data["vision_tower_reverse"]
        content += '<div class="evidence-block"><h2>图像尺寸变化时，计数怎样变化？</h2><p class="section-intro">11 个方形图像尺寸点均来自同轮归档。这里展示输入图像下的记录计数，保留低尺寸平台和后续增长。</p>'
        content += measurement_bars([(f"{size}×{size} px", count) for size, count in curve["curve"].items()], "归档输入计数 / token", "2026-09-17 · OpenRouter")
        content += interpretation("小图存在计数平台，较大图像的记录计数随尺寸增长。归档公式为 max(22, ceil(H/28)² + 6)，11 个点均吻合。", "这个计费形状提供 Qwen2-VL 家族视觉塔的线索。实际编码结构与底层模型身份仍需独立验证。") + '</div>'
        census = data["opencode_free_tier_census"]
        breakdown = census["breakdown"]
        census_rows = [("返回 401 CreditsError", breakdown["paid_401_creditserror"]), ("完成免费模型测量", breakdown["free_measured"]), ("429 限流", len(breakdown["rate_limited_429_console_provider"])), ("上游 500", len(breakdown["upstream_500"])), ("400 模型不可用", len(breakdown["upstream_400_model_unavailable"])), ("读取超时", len(breakdown["read_timeout"]))]
        content += '<details class="evidence-fold" id="census"><summary>另一个网关的目录普查：被列出，是否就可调用？</summary><p class="section-intro">OpenCode Zen · 2026-09-17。与本页主调查的 OpenRouter 入口分开记录；不是当前在线状态。</p>' + table(["当时的返回结果", "目录条目数"], census_rows) + f'<p class="data-caption">当次目录 {census["catalog_size"]} 项，使用 12 条短探针子集。授权、额度、限流和上游错误均影响可达性，不能直接解释为模型能力。</p></details>'
        return content
    elif slug == "pixel-canary":
        peers = read_json("case-pixel-canary-opencode-measurements.json")["clean_experiment"]["analysis"]["peers"]
        google = read_json("case-pixel-canary-google-measurements.json")["analysis"]["models"]
        content = '<div class="evidence-block"><h2>调查覆盖了什么，为什么还没有身份结论？</h2><div class="coverage-grid"><div><strong>24 条</strong><span>目标有效文本探针</span></div><div><strong>21 套</strong><span>公开本地词表</span></div><div><strong>4 + 1 个</strong><span>不同条件的具名对照</span></div></div>'
        baseline = data["analysis"]["baseline_values"]
        content += interpretation("目标三次基线计数均为 " + " / ".join(map(str, baseline)) + "，24 条探针取得有效计数。21 套候选没有完整匹配。", "候选覆盖范围是本次实验边界。计数规则、未公开词表和未覆盖模型仍有多种解释，供应商和权重身份保持未知。") + '</div>'
        rows = [(name, item["valid_probe_count"], f'{item["exact_matches"]}/{item["requested_probe_count"]}', f'{len(item["baseline_values"])} 次相同') for name, item in peers.items()]
        content += '<div class="evidence-block"><h2>四个跨网关对照：每个只测四条短探针</h2><p class="section-intro">目标来自 Cline，具名对照来自 OpenCode。剔除全局指令导致基线漂移的早期批次后，各对照基线均稳定。</p><div class="table-scroll">' + table(["OpenCode 具名入口", "有效探针", "与目标相同", "本模型基线"], rows) + '</div>'
        content += interpretation("LongCat、Ling 和 Nemotron 为 0/4；Muse 为 1/4，没有完整一致的差分向量。", "本组短探针不支持相同计数签名；跨网关预处理差异仍保留，不能由此扩大为排除整个厂商。") + '</div>'
        target = google["stealth/pixel-canary"]
        gemini = google["cline-free/gemini-3.8-flash"]
        rows = [(key, value, gemini["relative_deltas"][key], "一致" if value == gemini["relative_deltas"][key] else "不同") for key, value in target["relative_deltas"].items()]
        content += '<div class="evidence-block"><h2>同网关 Gemini 对照：改用共同中文基准</h2><p class="section-intro">两者均通过官方 Cline 客户端。Gemini 的空文本基线触发追问且未完成，因此改用重复的中文样本，比较相对它的三个增量。</p><div class="table-scroll">' + table(["相对中文样本的探针", "Pixel Canary 增量", "Gemini 增量", "结果"], rows) + '</div>'
        content += interpretation(f'两侧中文基准各重复 {len(target["anchor_values"])} 次且稳定：Pixel 为 ' + " / ".join(map(str, target["anchor_values"])) + '，Gemini 为 ' + " / ".join(map(str, gemini["anchor_values"])) + "。三项相对差分 0/3 一致。", "同网关与同客户端减少了一部分差异，仍不能证明模型专属计数层相同。这组对照不支持当前两个入口具有同一计数签名。") + '<p class="data-caption">本组 3 项相对差分与上组 4 条短探针使用不同基准，分别保留分母，不合并成统一命中率。</p></div>'
    else:
        raise ValueError(f"No focus renderer for case {slug}")
    content += '<details class="evidence-fold" id="tokenizer-evidence"><summary>展开词表与逐条探针明细</summary>' + evidence(case) + '</details>'
    return content


def home():
    omen = read_json("case-omen-alpha-measurements.json")
    first = omen["tokenizer_round1_16_probes"]
    second = omen["tokenizer_round2_8_probes"]
    observed = list(first["api_delta"].items()) + list(second["api_delta"].items())
    local = first["local_T_probe"]["glm5"] + second["glm5_delta"]
    exact = sum(value == peer for (_, value), peer in zip(observed, local))
    scale = max(value for _, value in observed)
    bars = "".join(
        f'<g><title>{escape(name)}：目标 {value} / GLM {peer} token</title>'
        f'<rect class="signature-bar" x="{22 + i * 16}" y="{170 - value / scale * 138:.2f}" width="6" height="{value / scale * 138:.2f}" rx="1"/>'
        f'<rect class="signature-peer" x="{30 + i * 16}" y="{170 - peer / scale * 138:.2f}" width="3" height="{peer / scale * 138:.2f}" rx="1"/></g>'
        for i, ((name, value), peer) in enumerate(zip(observed, local))
    )
    rows = []
    for index, case in enumerate(CASES, 1):
        href = f'cases/{case["slug"]}/index.html'
        clues = "".join(f'<li>{escape(clue)}</li>' for clue in case["clues"])
        rows.append(f'''<a class="archive-row" href="{href}"><div class="archive-top"><span class="case-number">CASE / {index:02d}</span>{status(case)}</div>
          <div class="archive-name">{escape(case["name"])}</div><div class="archive-title">{escape(case["title"])}</div><ul class="archive-clues">{clues}</ul>
          <div class="archive-bottom"><div><span class="archive-gateway">{escape(case["gateway"])}</span><span class="archive-date">{escape(case["date"])}</span></div><span class="card-arrow" aria-hidden="true">↗</span></div></a>''')
    file_count = len({file[0] for case in CASES for file in case["files"]})
    dimension_count = len(CASES[0]["fingerprint"])
    content = f'''<section class="home-stage"><div class="container home-hero" aria-labelledby="hero-title">
      <div class="hero-copy"><p class="eyebrow">OPEN MODEL FORENSICS <span class="eyebrow-divider">/</span> 开放模型指纹研究</p><h1 id="hero-title">模型隐去名字。<br><span>线索依然可见。</span></h1><p class="hero-description">从一次 API 响应，追踪匿名模型的真实线索。<br>把词表、能力与入口行为，放回可核查的证据里。</p><div class="hero-actions"><a class="button button-primary" href="#archive">探索调查档案 <span aria-hidden="true">↗</span></a><a class="text-link" href="guide/index.html">指纹怎么读 <span aria-hidden="true">→</span></a></div><p class="hero-footnote"><span class="signal-dot"></span>公开测量 · 原始数据 · 保留未知</p></div>
      <figure class="signature-panel"><div class="signature-top"><span>FINGERPRINT / 004</span><span class="specimen-label">归档样本</span></div><div class="signature-heading"><div><span class="signature-label">TOKENIZER DIFFERENTIAL</span><h2>omen-alpha</h2></div><div class="signature-score">{exact}<span>/{len(observed)}</span><small>与 GLM 词表一致</small></div></div>
        <svg class="signature-chart" viewBox="0 0 424 198" role="img" aria-labelledby="signature-title signature-desc"><title id="signature-title">omen-alpha 的 24 条探针计数差分</title><desc id="signature-desc">每组橙色条表示 API 输入增量，灰色条表示 GLM 本地词表计数。24 条均一致；这不等于唯一模型身份。条形按本组最高值 {scale} token 缩放。</desc><path class="signature-grid" d="M16 32H410 M16 101H410 M16 170H410"/>{bars}<text x="22" y="192">PROBE 01</text><text x="337" y="192">PROBE 24</text></svg>
        <figcaption><div class="signature-legend"><span><i></i>API 增量</span><span><i class="peer-dot"></i>GLM 本地词表</span></div><p>词表吻合是一条线索，<strong>不等于唯一身份。</strong></p></figcaption><a class="signature-link" href="cases/omen-alpha/index.html">检查完整证据链 <span aria-hidden="true">↗</span></a>
      </figure></div></section>
      <div class="container"><div class="archive-overview" aria-label="档案概况"><div><strong>{len(CASES):02d}</strong><span>公开调查档案</span></div><div><strong>{dimension_count:02d}</strong><span>指纹观察维度</span></div><div><strong>{file_count:02d}</strong><span>原始 JSON 记录</span></div><a href="methods/index.html"><span>让每个判断<br>都有据可查</span><span aria-hidden="true">↗</span></a></div>
      <section id="archive" class="archive" aria-labelledby="archive-heading"><div class="section-heading"><div><p class="eyebrow">THE CASE FILES</p><h2 id="archive-heading">每个匿名入口，都有一份档案。</h2></div><span class="caption">按首次测量时间倒序</span></div><div class="archive-grid">{''.join(rows)}</div></section>'''
    comparison = '<section class="case-comparison"><div class="section-heading"><div><p class="eyebrow">EVIDENCE COVERAGE</p><h2>各案测到了哪些维度？</h2></div><a class="text-link" href="guide/index.html">理解这些指标 →</a></div><p class="section-intro">结果属于各自的入口、日期与样本，不汇总为统一身份分数。</p><div class="table-scroll">' + table(["案例 / 日期", "词表线索", "上下文", "图像与视频", "工具调用"], [[case["name"] + " / " + case["date"]] + [row[1] for row in case["fingerprint"][:4]] for case in CASES]) + '</div></section>'
    content += comparison + '''<section class="further-reading"><a class="research-strip" href="updates/index.html"><p class="eyebrow">01 / MODEL UPDATES</p><h3>下一个新模型，<br>可能已留下线索。</h3><p>追踪公开目录、官方仓库与来源消息，核对每次变化的依据。</p><span class="text-link">查看模型更新 ↗</span></a><a class="research-strip research-dark" href="research/issue9/index.html"><p class="eyebrow">02 / RESEARCH NOTE</p><h3>同样的模板开销，<br>意味着同一个模型？</h3><p>六个候选都是 +15 token。这次实验，收窄了早期的排除结论。</p><span class="text-link">阅读 Issue #9 实验 ↗</span></a></section></div>'''
    render_page("index.html", "匿名模型调查档案", "匿名模型的公开指纹调查：词表、上下文、多模态证据与原始测量数据。", content, body_class="home-page")


def evidence_links(content):
    links = []
    def heading(match):
        title = match[1]
        anchor = "evidence-" + slugify_unicode(unescape(re.sub(r"<[^>]+>", "", title)), "-")
        links.append(f'<a href="#{escape(anchor)}">{title}</a>')
        return f'<h2 id="{escape(anchor)}">{title} <a class="heading-link" href="#{escape(anchor)}" aria-label="本段链接">#</a></h2>'
    return re.sub(r"<h2>(.*?)</h2>", heading, content), links


def case_page(case):
    page = f'cases/{case["slug"]}/index.html'
    evidence_html, evidence_nav = evidence_links(focus_evidence(case))
    fingerprint = "".join(f'<div class="fingerprint-row"><dt>{escape(label)}</dt><dd><strong>{escape(value)}</strong><p>{escape(note)}</p></dd></div>' for label, value, note in case["fingerprint"])
    unknowns = "".join(f'<li>{escape(item)}</li>' for item in case["unknowns"])
    protocol = "".join(f'<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>' for label, value in case["protocol"])
    timeline = "".join(f'<li><span class="timeline-date">{escape(date)}</span><div><h3>{escape(title)}</h3><p>{escape(text)}</p></div></li>' for date, title, text in case["timeline"])
    source = f'docs/case-{case["slug"]}.zh-CN.md'
    content = f'''<div class="container"><section class="page-hero case-hero">
      <div class="breadcrumbs"><a href="{relative(page, 'index.html')}#archive">调查档案</a><span aria-hidden="true">/</span><span>{escape(case['name'])}</span></div>
      <p class="eyebrow">CASE FILE / {escape(case['gateway'])}</p><h1>{escape(case['name'])}</h1><p class="case-title">{escape(case['title'])}</p><p class="case-description">{escape(case['summary'])}</p>
      <div class="case-meta"><span>结论范围 / {status(case)}</span><span>历史测量 / {escape(case['date'])}</span></div><p class="case-open-question"><span>尚未确认</span>{escape(case['unknowns'][0])}</p><dl class="endpoint-meta"><div><dt>调用模型 ID</dt><dd>{escape(case['model_id'])}</dd></div><div><dt>主调查入口</dt><dd>{escape(case['endpoint'])}</dd></div></dl><nav class="case-jumps" aria-label="本页目录"><a href="#fingerprint">指纹摘要 ↓</a><a href="#evidence">关键证据 ↓</a><a href="#timeline">判断过程 ↓</a><a href="#reproduce">复现与数据 ↓</a></nav></section>
      <section id="fingerprint" class="fingerprint-section"><div class="section-heading"><h2>本次调查的指纹摘要</h2><span class="caption">观测只针对所列入口、日期与测试范围</span></div><dl class="fingerprint-summary">{fingerprint}</dl><aside class="unknown-block"><h3>尚未确认</h3><ul>{unknowns}</ul></aside></section>
      <section id="evidence" class="evidence-section"><nav class="evidence-nav" aria-label="关键证据目录">{"".join(evidence_nav)}</nav>{evidence_html}</section><section id="timeline" class="timeline-section"><h2>判断是怎样改变的</h2><ol class="timeline">{timeline}</ol></section>
      <aside class="report-update"><strong>阅读原报告前</strong><p>报告保留历史判断。词表、计数、能力与权重身份需要分别解释；固定图像计数、有限重复及网关上限不单独证明架构、后端数量或代际。以下原文中的更强表述以本页摘要及报告更正为准。</p></aside><details id="report" class="report-fold"><summary>完整调查报告 <span>保留原始过程、失败对照与后续修正</span></summary>{report(source, page)}</details>
      <section id="reproduce" class="reproduce-section"><h2>复现这份证据，需要哪些条件？</h2><p class="section-intro">按归档的输入、参数和计数口径设计实验。历史入口的当前可达性需要重新检查；原始测量与当时的判断过程可直接核查。</p><dl class="protocol-list">{protocol}</dl><div class="hero-actions"><a class="text-link" href="#report">查看报告中的完整条件 ↓</a><a class="text-link" href="{relative(page, 'methods/index.html')}">安装工具与运行探针 →</a></div></section>
      {data_files(case['files'], page)}<div class="related-case"><a href="{relative(page, 'index.html')}#archive">← 返回全部调查</a><a href="{relative(page, 'guide/index.html')}">这些指纹应该怎样读 →</a></div></div>'''
    render_page(page, f"{case['name']} 调查", case["summary"], content, body_class="case-page")


def guide():
    content = '''<div class="container"><section class="methods-hero"><p class="eyebrow">READING THE EVIDENCE</p><h1>指纹能告诉你什么，<br>还缺哪些证据？</h1><p class="hero-description">从一次具体入口的测量出发，分开阅读实测记录、综合判断与未知问题。颜色、计数和响应都需要带着时间与测试条件一起看。</p></section>
      <nav class="guide-jumps" aria-label="指纹问题目录"><a href="#vocab">词表</a><a href="#context">上下文</a><a href="#vision">图像与视频</a><a href="#tools">工具调用</a><a href="#counts">模板与计数</a><a href="#peers">具名对照</a><a href="#gateway">入口与可达性</a></nav>
      <section id="vocab" class="guide-section"><div><p class="eyebrow">词表线索</p><h2>计数吻合，能判断到哪一步？</h2></div><div><p>Token 是模型处理文本时的切分单位；相同文字使用不同词表，可能得到不同计数。先用基线消掉固定开销，再比较中文、代码、emoji 和其他文本的计数差分。</p><p>精确匹配数必须带着有效探针分母一起读。平均绝对误差（MAE）描述本组计数差异，完整匹配提供词表家族线索，具体版本仍需独立证据。</p><p>生成侧还可检查是否原样复述，以及报告的输出 token 数与可见文本计数的差额；差额本身不直接等于隐藏思考。</p><a href="../cases/space-bunny/index.html#evidence">看 MiniMax 家族线索与代际未知 →</a></div></section>
      <section id="context" class="guide-section"><div><p class="eyebrow">长上下文</p><h2>接受长输入，是否也读到了内容？</h2></div><div><p>输入成功说明至少到达了这个长度。随后在头、中、尾埋入不同代码，检查能否精确找回，进一步验证本组读取结果。</p><p>最长成功输入、实际检索结果和失败原因分开保留。请求体限制、超时和限流都可能阻断继续测试；因此“成功测到 ≥969K”与“最大容量为 969K”是不同结论。</p><a href="../cases/omen-alpha/index.html#evidence">看成功输入与网关限制如何分开记录 →</a></div></section>
      <section id="vision" class="guide-section"><div><p class="eyebrow">图像与视频</p><h2>接收、答对和空输出分别意味着什么？</h2></div><div><p>先看请求是否接受，再核对真实输入颜色与实际回答。HTTP 200、明确回答、空输出和错误各自保留样本数。</p><p>不同尺寸下的计数变化提供编码与计费方式的线索。固定开销、尺寸平台或增长曲线不能单独确认架构；红蓝样本答对也不能证明广泛的图像理解能力。</p><p>原生 video_url、其他视频内容形状与视频塞入图像字段是不同测试。某种形状在网关被拒绝，只描述这条输入路径。</p><a href="../cases/omen-alpha/index.html#evidence">看 24 次接收成功与 16 次明确答对 →</a></div></section>
      <section id="tools" class="guide-section"><div><p class="eyebrow">工具语义</p><h2>返回调用，是否遵守了请求设置？</h2></div><div><p>分别检查 none、auto、required 下是否返回调用，调用数量、函数名、参数和并行行为是否符合任务与设置。</p><p>返回 tool_calls 是调用声明。记录中没有执行外部工具时，不把它描述为成功完成天气或时间查询。none 仍返回调用则说明这条入口没有遵守预期语义。</p><a href="../cases/union-alpha/index.html#evidence">看 union-alpha 的三种设置对照 →</a></div></section>
      <section id="counts" class="guide-section"><div><p class="eyebrow">模板与重复采样</p><h2>多出来的 token，来自哪个参照？</h2></div><div><p>“相对本地词表的差额”和“相对同网关具名模型的差额”使用不同参照。每个开销都应标明输入、候选词表和计数口径。</p><p>相同输入重复测量后出现多种值，可以先记录分布，再考虑多后端、重计或路由等解释。数值种类并不是模型数量；不同轮次的变化也应单独保留。</p><p>本地模板序列化描述发布格式，托管用量描述入口返回的计数。Issue #9 的六个候选在显式 system 条件下都为 +15，无法据此唯一识别模型。</p><a href="../research/issue9/index.html">读本地模板实验与剩余问题 →</a></div></section>
      <section id="peers" class="guide-section"><div><p class="eyebrow">具名模型对照</p><h2>两份结果真的在比较同一件事吗？</h2></div><div><p>核对是否同网关、同文本、同参数、同时间段和同计数口径。原生客户端的缓存计数可能与直接 API 不同，必须先明确基线如何抵消固定内容。</p><p>四条短探针、三项相对差分和完整 24 条实验分别保留分母。对某套词表或某个具名入口不匹配，不能扩展为排除整个厂商。</p><a href="../cases/pixel-canary/index.html#evidence">看跨网关与同网关的两组对照 →</a></div></section>
      <section id="gateway" class="guide-section"><div><p class="eyebrow">入口与可达性</p><h2>目录里列出了，就能实际调用吗？</h2></div><div><p>目录列表与实际请求结果分开记录。授权、额度、限流、上游错误和网络超时都可能影响可达性；一次目录抽样只描述当时的入口条件。</p><p>错误码、响应原文与参数接受方式还提供服务适配线索。错误风格不能直接当作底层权重或模型架构。</p><a href="../cases/union-alpha/index.html#census">看 71 个目录条目的历史抽样 →</a></div></section>
      <details class="evidence-fold guide-labels"><summary>库里的便捷标签怎样解读？</summary>'''
    content += table(["输出标签", "先展示的实测", "归因边界"], [
        ["count_variation", "相同文本出现多个计数值", "后端混用、重计与路由仍需进一步区分"],
        ["empty_output", "可见回答为空", "检查实际 finish_reason、reasoning 字段与输出预算"],
        ["survey.best", "本组计数最接近的候选", "没有完整匹配时，身份仍可能未知"],
    ])
    content += '''</details><div class="related-case"><a href="../index.html#archive">← 回到调查档案</a><a href="../methods/index.html">安装工具与开始实验 →</a></div></div>'''
    render_page("guide/index.html", "指纹怎么读", "按词表、上下文、多模态、工具、计数与网关对照，理解匿名模型调查的实测、判断和未知问题。", content, active="guide")


def methods():
    content = '''<div class="container"><section class="methods-hero"><p class="eyebrow">RUN & REPRODUCE</p><h1>开始一次能核查的调查。</h1><p class="hero-description">准备明确的 API 入口、模型 ID、可用凭据和候选词表。先完成一组小探针，再围绕剩余问题补证据。</p><p class="section-intro">下面以 OpenAI 兼容的 chat 入口为例；原生客户端与历史案例的特殊参数，按对应报告中的条件处理。</p><a class="text-link" href="../guide/index.html">先了解各类结果能说明什么 →</a></section>
      <section id="first-run"><div class="section-heading"><h2>开始第一组探针</h2><span class="caption">运行于你的本地环境</span></div><ol class="steps">
        <li><h3>获取工具与候选词表</h3><pre><code>git clone https://github.com/majiayu000/stealthprint.git
cd stealthprint
pip install -e ".[all]"
./fetch_tokenizers.sh</code></pre><p>候选分词器下载到 tok/。添加一份公开 tokenizer.json 即可增加一个候选；本地对照不会额外调用模型。</p></li>
        <li><h3>明确要测量的入口</h3><pre><code>export STEALTHPRINT_BASE_URL="https://api.example.com/v1"
export STEALTHPRINT_MODEL="mystery-model"
export STEALTHPRINT_API_KEY="your-api-key"
export STEALTHPRINT_LANG=zh</code></pre><p>替换为你的端点、模型 ID 与凭据。目标 API 请求可能计费，重复采样和其他层会增加请求。</p></li>
        <li><h3>比较第一组词表计数</h3><pre><code>stealthprint tokenizer --tokenizers tok/ --json</code></pre><p>先看成功探针数量，再看 exact / total 与 mae。stdout 只输出 JSON，进度写入 stderr；可加 --record requests.jsonl 保存逐请求原始材料；完整匹配只说明本组差分相同。</p></li>
        <li><h3>围绕剩余问题补证据</h3><pre><code># 计数不稳定时：重复采样
stealthprint tokenizer --tokenizers tok/ --repeats 3

# 比较模板开销
stealthprint wrapper

# 需要视觉证据时：颜色与尺寸实验
stealthprint vision</code></pre><p>使用 --record 保存入口、时间、请求、原始响应与失败；L1 的 JSON 还记录探针和词表哈希。选择下一层前，先明确它要证实或排除什么。</p></li>
      </ol></section><details class="evidence-fold"><summary>根据剩余问题选择更多探针</summary><pre><code># 原样复述与生成侧计数
stealthprint echo --tokenizers tok/

# 多轮模板积累
stealthprint wrapper --turns 4

# 成功输入边界与长上下文检索
stealthprint context

# 参数与错误响应
stealthprint errors

# tool_choice 与并行调用声明
stealthprint tools

# 视频内容形状
stealthprint video

# 同网关 GLM 具名模型对照
stealthprint catalog --family glm

# 限制模型数量的目录抽样
stealthprint survey --max-models 2 --out census.jsonl</code></pre><p class="data-caption">目标 API 请求可能计费；长输入、重复采样和目录抽样都会增加请求。--max-models 限制模型数量，不限制金额。</p></details><section class="method-notes"><h2>保存哪些材料，才能核查？</h2><ul>
        <li><strong>对象与时间。</strong> 完整入口、调用模型 ID、协议路径和测量时间。</li>
        <li><strong>输入与参数。</strong> 基线、逐条文本、采样条件、输出预算及客户端版本。</li>
        <li><strong>原始响应。</strong> usage、finish_reason、实际回答、错误和被剔除的尝试；缺失值不补成零。</li>
        <li><strong>对照来源。</strong> 本地 tokenizer 的 revision 与哈希，具名模型的入口和实际测试范围。</li>
        <li><strong>判断与修正。</strong> 哪条证据改变了推断，以及后续还有哪些待验证问题。</li>
      </ul><a class="text-link" href="https://github.com/majiayu000/stealthprint/blob/main/README.zh-CN.md">完整安装与命令说明 ↗</a></section></div>'''
    render_page("methods/index.html", "使用与复现", "安装 Stealthprint、运行第一组词表探针，并保存入口、参数、响应与对照来源以便复现实验。", content, active="methods")


def research():
    page = "research/issue9/index.html"
    data = read_json("issue9-template-results.json")
    rows = []
    successes = errors = 0
    for model in data["models"]:
        default = model["variants"]["default"]
        rows.append((model["repo"], default["base"]["overhead"], default["explicit_system"]["overhead"], default["two_turns"]["overhead"]))
        for variant in model["variants"].values():
            for sample in variant.values():
                if sample["status"] == "ok":
                    successes += 1
                elif sample["status"] == "template_error":
                    errors += 1
    content = f'''<div class="container"><section class="methods-hero"><p class="eyebrow">RESEARCH NOTE / ISSUE #9</p><h1>相同模板开销，<br>能说明相同身份吗？</h1><p class="hero-description">六个公开 tokenizer 在相同显式 system 输入下的额外 token 数都为 +15。模板描述提示格式，单独依靠这个数无法唯一识别模型。</p><div class="case-meta"><span class="status">本地实验</span><span>2026-09-29 / 固定 revision</span><span>实时推理实验未执行 · 已停止计划</span></div></section>
      <div class="stats"><div class="stat"><p class="stat-label">公开 tokenizer</p><p class="stat-value">{len(data['models'])}</p><p class="stat-note">每份文件记录 SHA-256 与 revision</p></div><div class="stat"><p class="stat-label">成功组合</p><p class="stat-value">{successes}</p><p class="stat-note">验证渲染后 token ID 序列</p></div><div class="stat"><p class="stat-label">模板异常</p><p class="stat-value">{errors}</p><p class="stat-note">错误保留在归档结果中</p></div></div>
      <section class="evidence-section"><h2>默认模板的本地开销</h2><p class="section-intro">开销 = 完整模板 token 数减去输入消息正文的裸 token 数。表格展示默认模板；思考开关变体与错误见下方完整报告和 JSON。</p><div class="table-scroll">{table(['公开模型', '单用户 base', '显式 system', '两轮消息'], rows)}</div><p class="data-caption">来源：issue9-template-results.json → models[].variants.default。这些是本地序列化计数，不是托管 API 用量。</p></section>
      <section class="research-article"><h2>实验记录与最终处置</h2>{report('docs/issue9-followup.md', page)}</section>
      {data_files([['issue9-template-inputs.json', '实验输入与固定 revision'], ['issue9-template-results.json', '全部计数、渲染文本与异常']], page)}</div>'''
    render_page(page, "Issue #9 模板实验", "六个公开 tokenizer 的可复现本地模板对照：53 个成功组合、2 个模板异常与未完成的推理实验。", content, active="research")


def updates():
    snapshot = json.loads((SITE / "updates.json").read_text(encoding="utf-8"))
    monitor = load_state(MONITOR_DIR)
    now = datetime.now(timezone.utc)

    def when(value):
        return parse_time(value).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") if value else "尚未成功"

    def evidence_link(path, text="下载原始证据"):
        return f'<a href="../data/monitor/{escape(path, quote=True)}" download>{text} ↓</a>' if path else ''

    source_names = {source["id"]: source["name"] for source in SOURCES}
    source_kinds = {source["id"]: source["kind"] for source in SOURCES}
    groups = {}
    historical_ids = set()
    for group in group_updates(snapshot):
        key = ("aitracker-feed", group["url"])
        groups[key] = {**group, "source_id": "aitracker-feed", "identity": group["url"], "events": []}
        for item in group["events"]:
            historical_ids.add(item["id"])
            groups[key]["events"].append({"id": item["id"], "kind": "reported", "historical": True,
                "date": item["date_published"], "published_at": item["date_published"],
                "after": {"summary": item["content_text"], "tags": item["tags"]}})
    for event in monitor["events"]:
        record = event["after"] or event["before"]
        key = (event["source_id"], record["url"] if event["source_id"] == "aitracker-feed" else event["entity_id"])
        if key not in groups:
            groups[key] = {"source_id": event["source_id"], "url": record["url"],
                           "model": record["name"], "maker": source_names[event["source_id"]], "identity": key[1], "events": []}
        if event["kind"] == "reported" and event["entity_id"] in historical_ids:
            groups[key]["events"] = [item for item in groups[key]["events"] if item["id"] != event["entity_id"]]
        groups[key]["events"].append({**event, "candidates": candidate_ids(event, source_kinds[event['source_id']], monitor["events"]),
                                     "date": event["published_at"] if event["kind"] == "reported" else event["observed_at"]})
    for group in groups.values():
        group["events"].sort(key=lambda item: parse_time(item["date"]), reverse=True)
    groups = sorted(groups.values(), key=lambda group: parse_time(group["events"][0]["date"]), reverse=True)

    def label(event):
        if event.get("candidates"):
            return "新标识 · 待核对", "blue"
        if event["kind"] == "reported":
            tags = event["after"].get("tags", [])
            if "release" in tags:
                return "发布消息 · 来源报告", "green"
            if "leak" in tags:
                return "泄露线索 · 来源报告", "amber"
            return "来源消息", "blue"
        return {"added": ("目录新增", "blue"), "changed": ("记录修订", "blue"),
                "missing": ("当前未观察到", "amber"), "restored": ("重新出现", "blue")}[event["kind"]]

    def summary(event):
        if event.get("candidates"):
            return "本来源新出现标识：" + "、".join(event["candidates"]) + "。请核对原始文件与既往记录；不代表正式发布或实际可调用。"
        if event["kind"] == "reported":
            return event["after"]["summary"]
        return {"added": "该条目首次出现在本来源的已保存目录中，尚未验证是否可调用。",
                "changed": "公开记录发生变化，可展开核对前后内容与原始依据。",
                "missing": "本轮完整目录中未观察到此条目，不能据此认定服务已下架。",
                "restored": "该条目重新出现在来源目录中，既往缺失记录仍予以保留。"}[event["kind"]]

    cards = []
    healthy_count = 0
    for source in SOURCES:
        row = monitor["sources"].get(source["id"], {})
        success, error = row.get("last_success_at"), row.get("error")
        stale_after = source["interval"] * 3
        stale = bool(success and (now - parse_time(success)).total_seconds() > stale_after)
        health = "采集失败" if error else "等待首次采集" if not success else "数据已过期" if stale else "采集正常"
        healthy_count += int(health == "采集正常")
        count = len(row.get("inventory", {}))
        unit = "个文件" if source["kind"] == "sdk" else "条记录"
        cards.append(f'''<article class="source-card" data-source-health data-last-success="{escape(success or '', quote=True)}" data-stale-after="{stale_after}" data-error="{str(bool(error)).lower()}">
          <div class="source-title"><a href="{escape(source['url'], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(source['name'])} ↗</a><strong data-health-label>{health}</strong></div>
          <p>最近成功 · <time>{when(success)}</time></p><p>{count} {unit} · {"已建立基线" if 'inventory' in row else "尚无基线"}</p>
          {('<p class="source-error">' + escape(error) + '</p>') if error else ''}
          {evidence_link(row.get('evidence'), '下载基线证据')}</article>''')
    rows = []
    for group in groups[:200]:
        anchor = "update-" + hashlib.sha256((group["source_id"] + ':' + group["identity"]).encode()).hexdigest()[:12]
        latest = group["events"][0]
        title, tone = label(latest)
        candidates = sorted({identity for item in group['events'] for identity in item.get('candidates', [])})
        signals = sorted({"candidate" if item.get("candidates") else "report" if item["kind"] == "reported" else "change"
                          for item in group["events"]})
        events = []
        for item in reversed(group["events"]):
            event_label, _ = label(item)
            detail = ''
            if item.get("observed_at"):
                detail += f'<p>本站观察时间 · {when(item["observed_at"])}</p>'
            if item.get("published_at"):
                detail += f'<p>来源发布时间 · {when(item["published_at"])}</p>'
            detail += evidence_link(item.get("evidence"))
            if item.get("before") is not None or (not item.get("historical") and item["kind"] != "reported"):
                before = escape(json.dumps(item.get('before'), ensure_ascii=False, indent=2))
                after = escape(json.dumps(item['after'], ensure_ascii=False, indent=2))
                detail += f'<details class="event-diff"><summary>核对前后记录</summary>{evidence_link(item.get("before_evidence"), "下载此前证据")}<h3>此前记录</h3><pre><code>{before}</code></pre><h3>本次记录</h3><pre><code>{after}</code></pre></details>'
            if item.get("historical"):
                detail += '<p>历史来源消息 · 本站首次观察时间未知</p><a href="../assets/updates.json" download>下载历史快照 ↓</a>'
            events.append(f'<li><time datetime="{escape(item["date"], quote=True)}">{when(item["date"])}</time><div><strong>{event_label}</strong><p>{escape(summary(item))}</p>{detail}<span class="event-id">事件 · {escape(item["id"])}</span></div></li>')
        query = escape(group["model"] + " " + group["maker"] + " " + source_names[group["source_id"]] + " " + " ".join(candidates), quote=True)
        rows.append(f'''<article class="update-row" id="{anchor}" data-update data-search="{query}" data-kind="{tone}" data-signals="{' '.join(signals)}">
          <div class="update-heading"><div><p class="update-maker">{escape(group['maker'])} · {escape(source_names[group['source_id']])}</p><h2>{escape(group['model'])}</h2></div><span class="status status-{tone}">{title}</span></div>
          <p class="update-summary">{escape(summary(latest))}</p>
          {('<p class="candidate-note">含新标识线索 · ' + escape('、'.join(candidates)) + ' · 展开消息经过核对</p>') if candidates else ''}
          <div class="update-meta"><time datetime="{escape(latest['date'], quote=True)}">最近记录 · {when(latest['date'])}</time><span>本站复测 · 暂无</span><a href="{escape(group['url'], quote=True)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a><a href="#{anchor}" aria-label="这条模型更新的永久链接">#</a></div>
          <details class="update-history"><summary>查看消息经过 · {len(events)} 条</summary><ol>{''.join(events)}</ol></details>
        </article>''')
    checked = monitor['checked_at']
    content = f'''<div class="container"><section class="updates-hero"><div class="updates-heading-row"><div><p class="eyebrow">THE MODEL WATCH / 模型观察站</p><h1>最近哪些模型<br>有了新变化？</h1><p class="hero-description">发现公开变化，保留来源与后续。<br>目录线索、发布消息和独立实测，各有依据。</p></div><div class="updates-stat"><strong>{len(SOURCES):02d}</strong><span>个观察来源</span><span class="signal-dot"></span></div></div><div class="updates-provenance"><span id="monitor-freshness" data-checked-at="{escape(checked or '', quote=True)}">最近检查 · {when(checked)}（北京时间）</span><a href="../data/monitor/state.json" download>下载完整监测数据 ↓</a><a href="../assets/updates.json" download>历史消息快照 · {when(snapshot['fetched_at'])}</a></div></section>
      <section class="source-section" aria-label="来源健康"><details class="source-details"><summary><strong>来源状态</strong><span id="source-health-summary">{healthy_count} / {len(SOURCES)} 个来源最近采集成功</span><span>展开查看 ↗</span></summary><p class="source-note">计划每 5 分钟检查；官方仓库每 15 分钟、构建包每小时采集，调度可能延迟。</p><div class="source-grid">{''.join(cards)}</div><p class="source-note">首次读取目录只建立基线。来源失败会保留上次证据；没有新记录不等于没有新模型。原始证据为 gzip JSON；构建包保留完整选定源码文件、版本与校验信息。</p></details></section>
      <div class="updates-layout"><section aria-label="模型更新列表"><div class="updates-controls"><label for="update-search">查找模型、厂商或来源<input id="update-search" type="search" placeholder="例如 DeepSeek、OpenRouter" autocomplete="off"></label><label for="update-signal">含以下记录<select id="update-signal"><option value="">全部记录</option><option value="candidate">新标识待核对</option><option value="change">配置与状态变化</option><option value="report">来源消息</option></select></label><label for="update-kind">消息类型<select id="update-kind"><option value="">全部消息</option><option value="green">发布消息</option><option value="amber">泄露或缺失</option><option value="blue">目录与其他变化</option></select></label></div><p id="update-count" class="update-count" aria-live="polite">{len(rows)} / {len(groups)} 组记录 · 完整历史可下载</p><noscript><p>所有记录在下方；交互筛选和实时过期提示需要 JavaScript。</p></noscript><p id="update-empty" hidden>没有找到匹配的记录，试试其他名称。</p>{''.join(rows) or '<p>基线已准备，后续变化将在这里显示。</p>'}</section>
      <aside class="updates-reading"><h2>这些记录说明什么？</h2><dl><dt>目录与 SDK 变化</dt><dd>某个公开来源发生了变化，不代表正式发布或实际可调用。</dd><dt>来源发布消息</dt><dd>保留来源的原始判断。AI Tracker 订阅通常比其 X 消息晚约 10 分钟。</dd><dt>本站复测</dt><dd>本页没有自动运行模型 API 测量。入口身份与能力需另外验证。</dd></dl><p>日期均为北京时间。来源发布时间、本站观察时间和 API 测量时间分别记录。</p><a class="text-link" href="../index.html#archive">阅读已有实测调查 →</a></aside></div></div>'''
    render_page("updates/index.html", "模型更新", "公开模型变化、原始依据、来源健康与消息时间线。", content, active="updates")


def build():
    assets = OUT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    inputs = [SITE / name for name in ("template.html", "styles.css", "site.js", "cases.json", "updates.json")]
    inputs += [MONITOR_DIR / "state.json"]
    inputs += sorted((ROOT / "docs").glob("*")) + [Path(__file__), ROOT / "stealthprint/updates.py"]
    hashes = {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs if path.is_file()}
    BUILD.update(built_at=datetime.now(timezone.utc).isoformat(), files=hashes,
                 source_digest=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    BUILD["commit"] = revision.stdout.strip() if revision.returncode == 0 else None
    BUILD["note"] = "File hashes identify the actual source, including uncommitted edits."
    (assets / "build.json").write_text(json.dumps(BUILD, indent=2) + "\n", encoding="utf-8")
    for filename in ("styles.css", "site.js"):
        shutil.copyfile(SITE / filename, assets / filename)
    shutil.copyfile(SITE / "updates.json", assets / "updates.json")
    monitor_out = OUT / "data" / "monitor"
    monitor_out.mkdir(parents=True, exist_ok=True)
    state = load_state(MONITOR_DIR)
    (monitor_out / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if (MONITOR_DIR / "evidence").exists():
        shutil.copytree(MONITOR_DIR / "evidence", monitor_out / "evidence")
    (OUT / ".nojekyll").touch()
    archive = OUT / "data"
    archive.mkdir(exist_ok=True)
    # Copy only the public measurement files referenced by these pages.
    files = {filename for case in CASES for filename, _ in case["files"]}
    files.update({"issue9-template-inputs.json", "issue9-template-results.json"})
    for filename in sorted(files):
        shutil.copyfile(ROOT / "docs" / filename, archive / filename)
    home()
    for case in CASES:
        case_page(case)
    guide()
    methods()
    research()
    updates()
    print(f"Built {len(CASES) + 5} pages and {len(files)} unchanged JSON archives in {OUT}")


def main():
    global OUT
    destination = OUT
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".site-build-", dir=destination.parent) as temporary:
            OUT = Path(temporary) / "dist"
            build()
            previous = Path(temporary) / "previous"
            if destination.exists():
                destination.replace(previous)
            try:
                OUT.replace(destination)
            except OSError:
                if previous.exists():
                    previous.replace(destination)
                raise
    finally:
        OUT = destination
    print(f"Built complete site at {destination}")


if __name__ == "__main__":
    main()
