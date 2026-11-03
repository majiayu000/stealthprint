"""Second-generation fingerprint layers and a profile library.

segmentation  -- the model's own token boundaries, read from per-token SSE streams
error_leaks   -- upstream error text: serving-stack and vendor signatures, limits
vision_grid   -- image token cost over sizes, fitted to ceil(w/p)*ceil(h/p)+c
profile/match -- one JSON profile per model; compare a target against a library

All results are plain dicts with stable English keys.
"""

import glob
import json
import math
import os
import re

from .client import require_openai
from .layers import png_b64, load_local_tokenizers

# ------------------------------------------------------------------ segmentation
ECHO_PROMPT = ("Output exactly the text between the markers, character for character, "
               "with nothing before or after it. Do not use code fences.\n<<<\n%s\n>>>")

SEGMENT_TEXTS = [
    ("en_pangram", "The quick brown fox jumps over the lazy dog."),
    ("en_words", "the of and to in is was for that with on as by at from his her they which"),
    ("en_prose", "Internationalization and localization are often abbreviated as i18n and l10n."),
    ("code_py", "def fibonacci(n: int) -> int:\n    if n < 2:\n        return n\n"
                "    return fibonacci(n - 1) + fibonacci(n - 2)"),
    ("json", '{"name": "pulse", "id": 12345, "tags": ["a", "b"], "ok": true}'),
    ("html", "<div class=\"a\" data-x='1'>&nbsp;</div>"),
    ("nums", "2026-11-03 3.14159 1000000 0.000123 -42 1e10 255.255.255.0"),
    ("zh_prose", "机器学习模型的分词器决定了文本如何被切分成词元，这对多语言能力影响很大。"),
    ("ja_prose", "東京の天気は明日晴れるでしょう。トークナイザーの違いを調べています。"),
    ("ko_prose", "안녕하세요 세계입니다. 인공지능이 세상을 바꾸고 있습니다."),
    ("th_prose", "กรุงเทพมหานคร ครับ ปัญญาประดิษฐ์กำลังเปลี่ยนโลก"),
    ("ru_prose", "Быстрая коричневая лиса прыгает через ленивую собаку."),
    ("de_prose", "Die Donaudampfschifffahrtsgesellschaftskapitänsmütze ist ungewöhnlich lang."),
    ("ar_prose", "الذكاء الاصطناعي يغير العالم بسرعة كبيرة."),
    ("hi_prose", "कृत्रिम बुद्धिमत्ता दुनिया को बदल रही है।"),
    ("fr_prose", "L'intelligence artificielle transforme rapidement le monde entier."),
]

# Strings whose single-token status separates vocabularies cheaply.
KEY_TOKENS = ["안녕하세요", "입니다", "กรุง", "机器", "学习", "分词", " fox", " jumps",
              "fibonacci", " International", "ization"]


def strip_ws_edges(pieces):
    pieces = list(pieces)
    while pieces and not pieces[0].strip():
        pieces.pop(0)
    while pieces and not pieces[-1].strip():
        pieces.pop()
    return pieces


def boundaries(pieces):
    """Character offsets after each piece."""
    out, pos = set(), 0
    for piece in pieces:
        pos += len(piece)
        out.add(pos)
    return out


def boundary_scores(reference, candidate):
    """(true_pos, false_pos, false_neg) of candidate boundaries vs reference."""
    a, b = boundaries(reference), boundaries(candidate)
    return len(a & b), len(b - a), len(a - b)


def f1(tp, fp, fn):
    if tp == 0:
        return 0.0
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def local_pieces(name, tk, text):
    """Text pieces a local tokenizer produces (bytes merged back to whole chars)."""
    if name in ("o200k_base", "cl100k_base") or hasattr(tk, "decode_single_token_bytes"):
        out, buf = [], b""
        for token in tk.encode(text, disallowed_special=()):
            buf += tk.decode_single_token_bytes(token)
            try:
                out.append(buf.decode("utf-8"))
                buf = b""
            except UnicodeDecodeError:
                continue
        return out
    enc = tk.encode(text, add_special_tokens=False)
    return [text[a:b] for a, b in enc.offsets]


def is_single_token(name, tk, text):
    if name in ("o200k_base", "cl100k_base") or hasattr(tk, "decode_single_token_bytes"):
        return len(tk.encode(text, disallowed_special=())) == 1
    return len(tk.encode(text, add_special_tokens=False).ids) == 1


def key_pattern_from_pieces(samples):
    """'1'/'.'/'?' per KEY_TOKENS from observed target pieces.

    '1' = seen as a whole piece; '.' = the string occurs in an echoed text but
    was split; '?' = not observable in this corpus."""
    seen_pieces = set()
    texts = []
    for row in samples:
        seen_pieces.update(row["pieces"])
        texts.append("".join(row["pieces"]))
    pattern = []
    for key in KEY_TOKENS:
        if key in seen_pieces:
            pattern.append("1")
        elif any(key in text for text in texts):
            pattern.append(".")
        else:
            pattern.append("?")
    return "".join(pattern)


def segmentation(client, tokenizers_dir="tok", texts=None, extra=None, verbose=True):
    """Collect the target's streamed pieces for echoed texts and compare token
    boundaries with each local tokenizer.

    per_token_check: on each sample, completion_tokens - reasoning_tokens -
    len(chunks) should be a small constant when the server flushes one token per
    chunk; the spread of that offset is reported, not assumed."""
    require_openai(client)
    texts = texts or SEGMENT_TEXTS
    samples, failures = [], []
    for name, text in texts:
        d, err = client.stream_chat([{"role": "user", "content": ECHO_PROMPT % text}],
                                    max_tokens=2048, extra=extra)
        if err:
            failures.append({"name": name, "error": str(err.get("body"))[:300], "http": err.get("http")})
            continue
        pieces = strip_ws_edges(d["chunks"])
        usage = d.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        ct, rt = usage.get("completion_tokens"), details.get("reasoning_tokens") or 0
        row = {"name": name, "text": text, "pieces": pieces, "exact_echo": "".join(pieces) == text,
               "n_chunks": len(d["chunks"]),
               "offset": None if ct is None else ct - rt - len(d["chunks"])}
        samples.append(row)
        if verbose:
            print("%-12s echo=%s chunks=%3d offset=%s" % (name, "Y" if row["exact_echo"] else "N",
                                                         row["n_chunks"], row["offset"]))
    usable = [r for r in samples if r["exact_echo"]]
    offsets = sorted({r["offset"] for r in usable if r["offset"] is not None})
    per_token = bool(usable) and len(offsets) == 1
    out = {"samples": samples, "failures": failures, "usable": len(usable),
           "per_token_chunks": per_token, "offsets": offsets,
           "key_tokens": KEY_TOKENS, "key_pattern": key_pattern_from_pieces(usable),
           "ranking": {}}
    if not per_token:
        out["warning"] = ("chunk counts do not track completion tokens with one constant offset; "
                          "chunks may batch several tokens and boundary scores are unreliable")
    if tokenizers_dir is None or not usable:
        return out
    local = load_local_tokenizers(tokenizers_dir)
    for name, tk in local.items():
        tp = fp = fn = identical = 0
        for row in usable:
            cand = local_pieces(name, tk, row["text"])
            a, b, c = boundary_scores(row["pieces"], cand)
            tp, fp, fn = tp + a, fp + b, fn + c
            identical += (b == 0 and c == 0)
        keys = "".join("1" if is_single_token(name, tk, k) else "." for k in KEY_TOKENS)
        out["ranking"][name] = {"f1": round(f1(tp, fp, fn), 4), "identical": identical,
                                "of": len(usable), "key_pattern": keys}
    if verbose:
        print("key pattern (target): %s  keys=%s" % (out["key_pattern"], KEY_TOKENS))
        for name, r in sorted(out["ranking"].items(), key=lambda kv: -kv[1]["f1"])[:12]:
            print("%-24s F1=%.3f identical=%d/%d keys=%s" % (name, r["f1"], r["identical"], r["of"],
                                                              r["key_pattern"]))
    return out


# ------------------------------------------------------------------ error leaks
LEAK_PROBES = [
    ("max_tokens_huge", {"max_tokens": 99999999}),
    ("max_tokens_zero", {"max_tokens": 0}),
    ("seed_string", {"seed": "abc"}),
    ("temperature_9", {"temperature": 9}),
    ("stop_six", {"stop": ["a", "b", "c", "d", "e", "f"]}),
    ("effort_bogus", {"reasoning_effort": "ultra"}),
]

# (tag, regex). Tags name the layer that wrote the text, not the model.
ERROR_SIGNATURES = [
    ("sglang", r"supports at most \d+ completion tokens|is longer than the model's context length"),
    ("vllm", r"maximum context length is \d+ tokens|decoder prompt \(length"),
    ("pd_disaggregated", r"[Pp]refill server"),
    ("rust_serde", r"invalid type: .*expected .* at line \d+ column \d+|untagged enum"),
    ("airlock_gateway", r"airlock_error"),
    ("openai_validation", r"Invalid '[a-z_]+'|integer_below_min_value|array_above_max_length"),
    ("openai_responses_api", r"max_output_tokens"),
    ("gemini_api", r"generation_config|maxOutputTokens|TYPE_INT32"),
    ("anthropic_api", r"maximum allowed number of output tokens"),
    ("minimax_api", r"\(2013\)|does not support max tokens >"),
    ("dashscope_api", r"Range of max_tokens should be"),
    ("deepseek_api", r"\[20015\]|valid range of max_tokens is"),
    ("moonshot_api", r"field in the request \(expected type"),
    ("openrouter_style", r"This endpoint's maximum context length"),
    ("cjk_message", r"[一-鿿]{4,}"),
    ("pydantic", r"validation error for|Input should be"),
]

LIMIT_PATTERNS = [
    ("max_completion", r"supports at most (\d+) completion tokens"),
    ("max_completion", r"must not exceed (\d+)"),
    ("max_completion", r"maximum supported value is (\d+)"),
    ("max_completion", r"> (\d+), which is the maximum"),
    ("max_completion", r"\[1, (\d+)\]"),
    ("max_completion", r"max tokens > (\d+)"),
    ("max_completion", r"less or equal to (\d+)"),
    ("context", r"context length \((\d+) tokens\)"),
    ("context", r"maximum context length is (\d+)"),
]


def classify_error(text):
    """Signature tags and numeric limits found in an error body."""
    text = text or ""
    tags = sorted({tag for tag, rx in ERROR_SIGNATURES if re.search(rx, text)})
    limits = {}
    for key, rx in LIMIT_PATTERNS:
        m = re.search(rx, text)
        if m and key not in limits:
            limits[key] = int(m.group(1))
    return {"tags": tags, "limits": limits}


def error_leaks(client, probes=None, extra=None, verbose=True):
    """Send invalid parameters and keep the raw error text plus its signatures."""
    require_openai(client)
    out = {"probes": {}, "tags": [], "limits": {}}
    for name, params in probes or LEAK_PROBES:
        body = dict(extra or {})
        body.update(params)
        max_tokens = body.pop("max_tokens", 16)
        d, err = client.chat([{"role": "user", "content": "hi"}], max_tokens=max_tokens, extra=body)
        if err:
            text = str(err.get("body"))
            cls = classify_error(text)
            out["probes"][name] = {"http": err.get("http"), "body": text[:600], **cls}
            out["tags"] = sorted(set(out["tags"]) | set(cls["tags"]))
            for k, v in cls["limits"].items():
                out["limits"].setdefault(k, v)
        else:
            out["probes"][name] = {"http": 200, "accepted": True}
        if verbose:
            p = out["probes"][name]
            print("%-16s => %s %s" % (name, p.get("http"), p.get("tags", "accepted")))
    if verbose:
        print("signatures: %s limits: %s" % (out["tags"], out["limits"]))
    return out


# ------------------------------------------------------------------ vision grid
VISION_SIZES = [(32, 32), (64, 64), (256, 256), (512, 512), (1024, 1024), (2048, 2048), (1024, 256)]


def fit_grid_formula(points, patches=range(8, 129), max_const=64):
    """Fit tokens = ceil(w/p) * ceil(h/p) + c over measured (w, h, tokens).

    Returns the best patch/constant with how many points it explains exactly,
    or a 'fixed' description when cost does not depend on size."""
    pts = [(w, h, v) for w, h, v in points if isinstance(v, int)]
    if not pts:
        return {"kind": "none"}
    if len({v for _, _, v in pts}) == 1:
        return {"kind": "fixed", "tokens": pts[0][2], "exact": len(pts), "of": len(pts)}
    best = None
    for p in patches:
        consts = {}
        for w, h, v in pts:
            c = v - math.ceil(w / p) * math.ceil(h / p)
            if 0 <= c <= max_const:
                consts[c] = consts.get(c, 0) + 1
        if not consts:
            continue
        c, hits = max(consts.items(), key=lambda kv: (kv[1], -kv[0]))
        cand = (hits, -p, p, c)
        if best is None or cand > best:
            best = cand
    if best is None:
        return {"kind": "unfitted", "points": pts}
    hits, _, p, c = best
    return {"kind": "grid", "patch": p, "const": c, "exact": hits, "of": len(pts),
            "formula": "ceil(w/%d)*ceil(h/%d)+%d" % (p, p, c)}


def vision_grid(client, sizes=None, rgb=(220, 30, 30), extra=None, verbose=True):
    require_openai(client)
    q = "What single color fills this image? Answer with one word."
    base, err = client.prompt_tokens([{"role": "user", "content": q}], max_tokens=64, extra=extra)
    if err:
        return {"error": "text-only control failed", "detail": str(err.get("body"))[:300]}
    rows = []
    for w, h in sizes or VISION_SIZES:
        msg = [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + png_b64((w, h), rgb)}}]}]
        d, err = client.chat(msg, max_tokens=64, extra=extra)
        if err:
            rows.append({"size": "%dx%d" % (w, h), "w": w, "h": h, "error": str(err.get("body"))[:200]})
        else:
            answer = (d["choices"][0]["message"].get("content") or "").strip()
            rows.append({"size": "%dx%d" % (w, h), "w": w, "h": h,
                         "tokens": d["usage"]["prompt_tokens"] - base, "answer": answer[:40]})
        if verbose:
            r = rows[-1]
            print("%-10s %s" % (r["size"], r.get("tokens", r.get("error"))))
    fit = fit_grid_formula([(r["w"], r["h"], r.get("tokens")) for r in rows])
    if verbose:
        print("fit: %s" % fit)
    return {"text_only_prompt_tokens": base, "rows": rows, "fit": fit}


# ------------------------------------------------------------------ profiles
def build_profile(model, l1=None, seg=None, leaks=None, vision=None, meta=None):
    """Assemble a comparable profile from whatever layers were measured."""
    prof = {"model": model, "meta": meta or {}}
    if l1:
        prof["l1_delta"] = l1.get("api_delta") or l1
    if seg:
        prof["segmentation"] = {"key_pattern": seg.get("key_pattern"),
                                "per_token_chunks": seg.get("per_token_chunks"),
                                "samples": [{"name": s["name"], "pieces": s["pieces"]}
                                            for s in seg.get("samples", []) if s.get("exact_echo")]}
    if leaks:
        prof["errors"] = {"tags": leaks.get("tags", []), "limits": leaks.get("limits", {})}
    if vision:
        prof["vision"] = {"fit": vision.get("fit"),
                          "tokens": {r["size"]: r.get("tokens") for r in vision.get("rows", [])}}
    return prof


def compare_profiles(a, b):
    """Per-layer agreement between two profiles (only layers both have)."""
    out = {}
    da, db = a.get("l1_delta"), b.get("l1_delta")
    if da and db:
        keys = [k for k in da if k in db]
        out["l1"] = {"exact": sum(da[k] == db[k] for k in keys), "of": len(keys)}
    sa, sb = a.get("segmentation"), b.get("segmentation")
    if sa and sb:
        pa, pb = {s["name"]: s["pieces"] for s in sa["samples"]}, {s["name"]: s["pieces"] for s in sb["samples"]}
        tp = fp = fn = 0
        for name in set(pa) & set(pb):
            x, y, z = boundary_scores(pa[name], pb[name])
            tp, fp, fn = tp + x, fp + y, fn + z
        if tp + fp + fn:
            out["segmentation_f1"] = round(f1(tp, fp, fn), 4)
    va, vb = (a.get("vision") or {}).get("tokens"), (b.get("vision") or {}).get("tokens")
    if va and vb:
        keys = [k for k in va if k in vb and isinstance(va[k], int) and isinstance(vb[k], int)]
        if keys:
            out["vision"] = {"exact": sum(va[k] == vb[k] for k in keys), "of": len(keys)}
    ea, eb = (a.get("errors") or {}).get("tags"), (b.get("errors") or {}).get("tags")
    if ea and eb:
        sa_, sb_ = set(ea), set(eb)
        out["error_jaccard"] = round(len(sa_ & sb_) / len(sa_ | sb_), 3)
    return out


def same_model_verdict(cmp):
    """'match' only when every compared hard layer agrees fully."""
    hard = []
    if "l1" in cmp:
        hard.append(cmp["l1"]["exact"] == cmp["l1"]["of"])
    if "vision" in cmp:
        hard.append(cmp["vision"]["exact"] == cmp["vision"]["of"])
    if "segmentation_f1" in cmp:
        hard.append(cmp["segmentation_f1"] >= 0.999)
    if not hard:
        return "no_overlap"
    return "match" if all(hard) else ("partial" if any(hard) else "different")


def load_library(path):
    lib = {}
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            prof = json.load(fh)
        lib[prof.get("model") or os.path.basename(f)[:-5]] = prof
    return lib


def match_profile(target, library):
    rows = []
    for name, prof in library.items():
        if prof is target or name == target.get("model"):
            continue
        cmp = compare_profiles(target, prof)
        l1 = cmp.get("l1", {})
        score = (l1.get("exact", 0) / l1["of"]) if l1.get("of") else 0.0
        rows.append({"model": name, "verdict": same_model_verdict(cmp), "l1_ratio": round(score, 3), **cmp})
    order = {"match": 0, "partial": 1, "different": 2, "no_overlap": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["l1_ratio"]))
    return rows
