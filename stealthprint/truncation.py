"""Token boundaries from max_tokens truncation, independent of usage counts.

A reply cut by max_tokens stops on a real token boundary however much hidden reasoning
used, and the tokens before the cut cannot exceed max_tokens. So echoing a fixed text
at many budgets tests a tokenizer even when usage is an estimate.

A candidate passes only if every cut is a boundary and no cut is over budget. Passing
is not unique proof: another tokenizer may split these texts the same way.
"""

import math

from .client import require_openai
from .layers import load_local_tokenizers, visible_lengths

PROMPT = ("Repeat the text between the markers exactly, character for character. "
          "Output only the text, no markers, no commentary.\n<<<\n%s\n>>>")

TEXTS = {
    "t1": ("Zhuangzi's unbelievably counterintuitive antidisestablishmentarianism: 魑魅魍魉、饕餮与麒麟 interoperate via HTTP/2 multiplexing; "
           "東京の量子コンピュータ研究所 reported 3.14159265 kWh, naïve façade Œuvre. Подмосковные вечера transliterated: Podmoskovnye. "
           "def __getattribute__(self, kwargs): return sorted(xs, key=lambda x: -x[1])[::-1] # TODO(refactor) "
           "한국어 형태소 분석기와 ภาษาไทยไม่มีช่องว่าง coexist; pneumonoultramicroscopicsilicovolcanoconiosis, Llanfairpwllgwyngyll, 0x7FFFDEADBEEF, 2026-11-04T19:46:00Z."),
    "t2": ("Weltschmerz und Kummerspeck: Rindfleischetikettierungsüberwachungsaufgabenübertragungsgesetz wurde aufgehoben. "
           "螭吻睚眦狻猊蒲牢囚牛椒图霸下负屃，龙生九子各有所好；鳜鱼桂花糯米藕。"
           "北海道の釧路湿原でタンチョウヅルが舞い、薔薇と檸檬と鬱蒼とした森を観測した。"
           "쀍뷁 똠방각하 괜찮아요, 닭갈비를 먹었습니다. "
           "ฉันกำลังเรียนรู้การเขียนโปรแกรมภาษาไทยอย่างตั้งใจ. नमस्ते दुनिया, यह एक परीक्षण है. مرحبا بالعالم الاستكشاف. "
           "std::unordered_map<std::string_view, absl::flat_hash_set<int64_t>> m_kConfigRegistry{}; SELECT COALESCE(NULLIF(x,''),'n/a') FROM t; "
           "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08, QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG, "
           "thyroparathyroidectomized floccinaucinihilipilification supercalifragilisticexpialidocious Xiuhtecuhtli Eyjafjallajökull."),
    "t3": ("Donaudampfschifffahrtsgesellschaftskapitän Schadenfreude Zwetschgenknödel; anticonstitutionnellement, hétérogénéité, 'Ñandú pingüino cigüeña'. "
           "爨龘饕鬻彠龢鸂鶒翡翠，蟋蟀蜈蚣蝙蝠蜘蛛；氤氲缱绻踟蹰彳亍。瀬戸内海の鰯と鯖、躊躇と憂鬱を齧歯類が嚙み砕く。"
           "뷁궯 꿻뚫 넓적다리 앉아서 읽었습니다; 얽히고설킨 삶. เศรษฐกิจพอเพียงกับปรัชญาสมัยใหม่. বাংলা ভাষার ব্যাকরণ. עברית מודרנית ומעניינת. Ελληνική φιλοσοφία και Αριστοτέλης. "
           "template<typename... Ts> constexpr auto operator<=>(const Foo<Ts...>&) const noexcept -> std::strong_ordering; ls -la | awk '{print $NF}' | xargs -I{} rm -rf {}; "
           "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi, 0b1011011101111, ZXhhbXBsZSBiYXNlNjQgdGV4dA==, "
           "hippopotomonstrosesquippedaliophobia dichlorodifluoromethane Popocatépetl Nagarjunakonda."),
}

# Lowest thinking first; None sends no reasoning_effort at all.
EFFORTS = ("none", "minimal", "low", None)
CALIBRATION_TOKENS = 128
START_TOKENS = 16
MAX_TOKENS_CAP = 4096
MAX_CONSECUTIVE_ERRORS = 3
# Fewer cuts than this rejects few candidates, so a pass says little.
MIN_CUTS_FOR_EVIDENCE = 30
# A stream is per-token only if its chunks track visible tokens and stay short.
MIN_CHUNK_TOKEN_RATIO = 0.7
MAX_MEDIAN_CHUNK_CHARS = 8
STREAM_TOKENS = 4096


def ask(client, text, max_tokens, effort=None, extra=None):
    """One truncated echo. Returns a row with the cut, or why there is none."""
    body = dict(extra or {})
    if effort:
        body["reasoning_effort"] = effort
    d, err = client.chat([{"role": "user", "content": PROMPT % text}], max_tokens=max_tokens, extra=body or None)
    if err:
        return {"n": max_tokens, "error": str(err.get("body"))[:300], "http": err.get("http")}
    choice = d["choices"][0]
    content = (choice["message"].get("content") or "").lstrip("\n")
    row = {"n": max_tokens, "finish": choice.get("finish_reason"), "chars": len(content)}
    if content and not text.startswith(content):
        row["deviated"] = True  # the model mistyped the echo: says nothing about tokens
    elif content and row["finish"] == "length":
        row["cut"] = len(content)
    return row


def pick_effort(client, text, extra=None, verbose=True):
    """Return the lowest accepted effort that leaves visible text, and what was tried."""
    tried, accepted = [], []
    for effort in EFFORTS:
        row = ask(client, text, CALIBRATION_TOKENS, effort=effort, extra=extra)
        tried.append({"effort": effort, **{k: row[k] for k in ("chars", "error") if k in row}})
        if verbose:
            print("effort %-8s => %s" % (effort, row.get("error", "%s chars" % row.get("chars")))[:120])
        if "error" in row:
            continue
        accepted.append(effort)
        if row.get("chars"):
            return effort, tried
    return (accepted[0] if accepted else None), tried


def ladder(client, text, effort=None, extra=None, start=START_TOKENS, step=2, max_requests=80, verbose=True):
    """Collect [max_tokens, cut] pairs, widening the budget while thinking eats it.

    Empty replies jump ahead by a quarter. The first reply with text then walks back to
    just after the last empty budget, so early cuts are not skipped. Stops on repeated
    errors, once the whole text fits twice, or at max_requests."""
    rows, observations = [], []
    n, floor, errors, complete, stop_reason = start, None, 0, 0, "max_requests"
    while len(rows) < max_requests:
        if n > MAX_TOKENS_CAP:
            stop_reason = "token_cap"
            break
        row = ask(client, text, n, effort=effort, extra=extra)
        rows.append(row)
        if "error" in row:
            errors += 1
            if errors >= MAX_CONSECUTIVE_ERRORS:
                stop_reason = "errors"
                break
            n += step
            continue
        errors = 0
        if row["finish"] != "length" and row["chars"]:
            complete += 1
            if complete >= 2:
                stop_reason = "complete"
                break
        if "cut" in row:
            observations.append([n, row["cut"]])
        if not row["chars"]:
            floor = n
            n += max(step, math.ceil(n / 4))
        elif floor is not None and n > floor + step:
            n, floor = floor + step, None
        else:
            floor = None
            n += step
    if verbose:
        print("ladder: %d requests, %d cuts, stop=%s" % (len(rows), len({c for _, c in observations}), stop_reason))
    stats = {"requests": len(rows), "empty": sum(1 for r in rows if r.get("chars") == 0),
             "deviated": sum(1 for r in rows if r.get("deviated")), "errors": sum(1 for r in rows if "error" in r),
             "stop": stop_reason, "last_error": next((r["error"] for r in reversed(rows) if "error" in r), None)}
    return observations, stats


def stream_boundaries(client, text, effort=None, extra=None):
    """Boundaries from one streamed echo, or None when chunks are not per-token."""
    body = dict(extra or {})
    if effort:
        body["reasoning_effort"] = effort
    d, err = client.stream_chat([{"role": "user", "content": PROMPT % text}], max_tokens=STREAM_TOKENS, extra=body or None)
    if err:
        return {"per_token": False, "error": str(err.get("body"))[:300]}
    joined = "".join(d["chunks"])
    lead = len(joined) - len(joined.lstrip())
    if joined.strip() != text:
        return {"per_token": False, "reason": "not an exact echo"}
    usage = d.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    visible = (usage.get("completion_tokens") or 0) - (details.get("reasoning_tokens") or 0)
    lengths = sorted(len(c) for c in d["chunks"] if c)
    median = lengths[len(lengths) // 2] if lengths else 0
    per_token = visible > 0 and len(d["chunks"]) >= MIN_CHUNK_TOKEN_RATIO * visible and median <= MAX_MEDIAN_CHUNK_CHARS
    out = {"per_token": per_token, "chunks": len(d["chunks"]), "visible_tokens": visible}
    if per_token:
        position, boundaries = -lead, set()
        for chunk in d["chunks"]:
            position += len(chunk)
            if position > 0:
                boundaries.add(position)
        out["boundaries"] = sorted(boundaries)
    return out


def score_observations(observations, lengths):
    """How one tokenizer's boundaries explain the cuts of one text."""
    cuts, boundaries = {c for _, c in observations}, set(lengths)
    over = sum(1 for n, c in observations if next((i + 1 for i, x in enumerate(lengths) if x >= c), 0) > n)
    return {"hit": len(cuts & boundaries), "cuts": len(cuts), "over": over}


def rank_tokenizers(observations_by_text, local, texts=None):
    """Combine scores across texts. No cuts means no candidate passes, not all of them."""
    texts = texts or TEXTS
    total = sum(len({c for _, c in rows}) for rows in observations_by_text.values())
    ranking = {}
    for name, tk in local.items():
        hit = over = 0
        for text_id, rows in observations_by_text.items():
            result = score_observations(rows, visible_lengths(name, tk, texts[text_id]))
            hit, over = hit + result["hit"], over + result["over"]
        ranking[name] = {"hit": hit, "cuts": total, "over": over}
    passing = sorted(n for n, r in ranking.items() if total and r["hit"] == total and r["over"] == 0)
    return {"cuts": total, "passing": passing, "evidence": "strong" if total >= MIN_CUTS_FOR_EVIDENCE else "weak",
            "ranking": ranking}


def truncation_probe(client, tokenizers_dir="tok", text_ids=("t3",), effort="auto", extra=None, step=2,
                     max_requests=80, stream=True, verbose=True):
    """Run the ladder per text, stream once per text, and rank local tokenizers."""
    require_openai(client)
    unknown = [t for t in text_ids if t not in TEXTS]
    if unknown:
        raise ValueError("unknown truncation text: %s (have %s)" % (unknown, sorted(TEXTS)))
    # Load candidates before any billable request.
    local = load_local_tokenizers(tokenizers_dir)
    tried = None
    if effort == "auto":
        effort, tried = pick_effort(client, TEXTS[text_ids[0]], extra=extra, verbose=verbose)
    elif effort == "default":
        effort = None
    out = {"effort": effort, "effort_tried": tried, "texts": {}}
    observations = {}
    for text_id in text_ids:
        rows, stats = ladder(client, TEXTS[text_id], effort=effort, extra=extra, step=step,
                             max_requests=max_requests, verbose=verbose)
        observations[text_id] = rows
        out["texts"][text_id] = {"observations": rows, "stats": stats}
        if stream and stats["stop"] != "errors":
            out["texts"][text_id]["stream"] = stream_boundaries(client, TEXTS[text_id], effort=effort, extra=extra)
    out.update(rank_tokenizers(observations, local))
    if not out["cuts"]:
        out["error"] = "no truncation cuts: %s" % next(
            (t["stats"]["last_error"] or t["stats"]["stop"] for t in out["texts"].values()), "no texts")
    if verbose:
        print("cuts=%d evidence=%s passing=%s" % (out["cuts"], out["evidence"], out["passing"]))
        worst = sorted(out["ranking"].items(), key=lambda kv: (kv[1]["cuts"] - kv[1]["hit"]) + kv[1]["over"])
        for name, row in worst[:8]:
            print("  %-22s hit %d/%d over=%d" % (name, row["hit"], row["cuts"], row["over"]))
    return out
