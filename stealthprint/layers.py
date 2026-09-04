"""Fingerprint layers. Each returns a plain dict (stable English keys) and
prints a human-readable summary via i18n.t()."""

import base64
import json
import os
import struct
import zlib

from .i18n import t

FILL = "The quiet harbor town woke slowly under a gray sky, and fishermen checked their nets. "
EMOJI_ZWJ = "👨‍👩‍👧‍👦 🏳️‍🌈 👍🏽"
SPECIAL_IMAGE = "<|begin_of_image|>"


def png_b64(size, rgb):
    """Solid RGB PNG as base64 (stdlib; no pillow)."""
    w, h = size

    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


def _err_text(err):
    if not err:
        return ""
    if isinstance(err, dict):
        return str(err.get("body") or err)
    return str(err)


_COLOR_REFUSAL = (
    "don't actually see",
    "do not actually see",
    "don't see any image",
    "do not see any image",
    "i don't see",
    "i do not see",
    "no image in",
    "cannot see the image",
    "can't see the image",
    "didn't receive an image",
    "no image was",
)


def _classify_multimodal(err, answer="", reasoning=""):
    if err:
        msg = _err_text(err)
        low = msg.lower()
        if "does not support image" in low:
            return "no_image_support", msg[:240]
        if "does not support video" in low or ("video" in low and "support" in low):
            return "no_video_support", msg[:240]
        if "messagecontent" in low or "untagged enum" in low:
            return "serde_array_content", msg[:240]
        if "[1210]" in msg or "[1214]" in msg:
            return "zhipu_numeric", msg[:240]
        return "http_error", msg[:240]
    ans = (answer or "").strip()
    rea = (reasoning or "").strip()
    compact = ans.lower().rstrip(".! ")
    if compact in ("red", "it's red", "it is red", "the image is red"):
        return "ok_red", ans[:120]
    if compact in ("blue", "it's blue", "it is blue", "the image is blue"):
        return "ok_blue", ans[:120]
    blob = ("%s %s" % (ans, rea)).lower()
    if any(p in blob for p in _COLOR_REFUSAL):
        return "no_visible_image", (ans or rea)[:120]
    if not ans:
        return "thinking_truncated", rea[:120]
    if "red" in compact and "blue" not in compact:
        return "ok_red", ans[:120]
    if "blue" in compact and "red" not in compact:
        return "ok_blue", ans[:120]
    return "ok_other", ans[:120]


# ------------------------------------------------------------------ L1 + L2
def tokenizer_differential(client, probes=None, tokenizers_dir="tok", verbose=True):
    """L1: delta = T(base+probe) - T(base) from usage.prompt_tokens, compared
    against local open tokenizer.json files + tiktoken o200k."""
    try:
        from tokenizers import Tokenizer
        import tiktoken
    except ImportError as e:
        raise RuntimeError(t("err.missing_dep", dep=e.name, pkgs="tokenizers tiktoken")) from e

    if probes is None:
        from .i18n import load_probes
        probes = load_probes()

    base = probes["base"]
    items = probes["probes"]

    if verbose:
        print(t("tok.querying", n=len(items)))
    base_api, err = client.prompt_tokens([{"role": "user", "content": base}])
    if err:
        raise RuntimeError("base request failed: %s" % err)

    api_delta = {}
    for name, text in items:
        pt, e = client.prompt_tokens([{"role": "user", "content": base + text}])
        if e:
            if verbose:
                print(t("tok.failed", name=name, err=e))
            continue
        api_delta[name] = pt - base_api
        if verbose:
            print("  %-14s prompt=%-6d delta=%d" % (name, pt, pt - base_api))

    local = {}
    if os.path.isdir(tokenizers_dir):
        for f in sorted(os.listdir(tokenizers_dir)):
            if f.endswith(".json"):
                try:
                    local[f[:-5]] = Tokenizer.from_file(os.path.join(tokenizers_dir, f))
                except Exception as e:
                    if verbose:
                        print(t("tok.skip", name=f, err=e))
    local["o200k_base"] = tiktoken.get_encoding("o200k_base")

    def ntok(name, tk, s):
        if name == "o200k_base":
            return len(tk.encode(s))
        return len(tk.encode(s, add_special_tokens=False).ids)

    if verbose:
        print()
        print(t("tok.table"))
        for name, _ in items:
            if name not in api_delta:
                continue
            vals = [ntok(n, tk, _) for n, tk in local.items()]
            print("%-14s | %s | %d" % (name, " ".join("%-8d" % v for v in vals), api_delta[name]))
        print()
        print(t("tok.ranking"))

    names = list(local)
    targets = [api_delta[n] for n, _ in items if n in api_delta]
    ranking = {}
    for n in names:
        vals = [ntok(n, local[n], text) for pname, text in items if pname in api_delta]
        exact = sum(1 for v, d in zip(vals, targets) if v == d)
        mae = sum(abs(v - d) for v, d in zip(vals, targets)) / len(targets)
        ranking[n] = {"exact": exact, "total": len(targets), "mae": round(mae, 2)}
        if verbose:
            mark = t("tok.match") if exact == len(targets) else ""
            print("%-14s exact=%2d/%d mae=%.2f%s" % (n, exact, len(targets), mae, mark))

    wrapper = {}
    for n in names:
        wrapper[n] = base_api - ntok(n, local[n], base)
    if verbose:
        print()
        print(t("tok.wrapper"))
        for n in names:
            print("%-14s wrapper=%+d" % (n, wrapper[n]))

    return {"api_delta": api_delta, "ranking": ranking, "wrapper": wrapper,
            "base_api_prompt_tokens": base_api}


def wrapper_constant(client, texts=None, verbose=True):
    """L2: prompt_tokens across prompt lengths; constant api-minus-local delta
    means a fixed chat template."""
    texts = texts or ["hi", "hello", "Say OK.",
                      "Please answer with a single word: yes or no, thanks."]
    if verbose:
        print(t("wrap.header"))
    out = {}
    for s in texts:
        pt, e = client.prompt_tokens([{"role": "user", "content": s}])
        if e:
            out[s] = {"error": e}
        else:
            out[s] = {"prompt_tokens": pt}
            if verbose:
                print("%-56r prompt_tokens=%d" % (s, pt))
    if verbose:
        print()
        print(t("wrap.hint"))
    return out


# ------------------------------------------------------------------ L3
def context_search(client, max_bytes=4_500_000, min_step=30_000, task=None, verbose=True):
    """L3: binary-search max working input size (capped by gateway body limit).
    Returns verified max in chars + prompt_tokens at that point."""
    task = task or "Answer with the single word OK and nothing else."

    def ok_at(chars):
        text = task + "\n\n" + FILL * (chars // len(FILL))
        pt, e = client.prompt_tokens([{"role": "user", "content": text}], timeout=300)
        return (True, pt, None) if e is None else (False, None, e)

    if verbose:
        print(t("ctx.header"))
    lo, hi = 10_000, max_bytes
    history = []
    while hi - lo > min_step:
        mid = (lo + hi) // 2
        good, pt, e = ok_at(mid)
        history.append({"chars": mid, "ok": good, "prompt_tokens": pt})
        if verbose:
            print("  %,d chars: %s" % (mid, ("OK pt=" + format(pt, ",")) if good else "FAIL " + str(e)[:160]), flush=True)
        if good:
            lo = mid
        else:
            hi = mid
    good, pt, _ = ok_at(lo)
    if verbose:
        print(t("ctx.max", chars=format(lo, ","), tokens=pt or 0))
    return {"max_verified_chars": lo, "max_verified_prompt_tokens": pt, "history": history}


def needle_test(client, prompt_tokens_size=500_000, needles=None, max_tokens=3000, verbose=True):
    """L3: hide three codes at head/mid/tail of a haystack sized ~prompt_tokens_size."""
    needles = needles or [
        (0.05, "ALPHA", "48219"), (0.5, "BRAVO", "70553"), (0.95, "CHARLIE", "91024")]
    units = max(1, int(prompt_tokens_size / (len(FILL) / 4.73)))
    positions = {int(units * f): (lab, code) for f, lab, code in needles}
    parts = []
    for u in range(units):
        if u in positions:
            lab, code = positions[u]
            parts.append("\nREMEMBER THIS EXACTLY: the vault code at checkpoint %s is %s. \n" % (lab, code))
        else:
            parts.append(FILL)
    q = ("Each of the three vault codes below is hidden in the text. Reply with only the "
         "three codes in order, comma separated.\n\n" + "".join(parts))
    if verbose:
        print()
        print(t("ctx.needle"))
    d, e = client.chat([{"role": "user", "content": q}], max_tokens=max_tokens, timeout=900)
    if e:
        if verbose:
            print(t("ctx.needle_fail", err=e))
        return {"error": e, "recovered": None}
    m = d["choices"][0]["message"]
    answer = (m.get("content") or "").strip()
    expected = ", ".join(code for _, _, code in needles)
    recovered = sum(1 for code in expected.split(", ") if code in answer)
    if verbose:
        print("prompt_tokens=%d finish=%s" % (d["usage"]["prompt_tokens"], d["choices"][0]["finish_reason"]))
        print("answer: %r" % answer)
        print(t("ctx.expected", codes=expected))
    return {"prompt_tokens": d["usage"]["prompt_tokens"], "answer": answer,
            "expected": expected, "recovered": "%d/%d" % (recovered, len(needles))}


# ------------------------------------------------------------------ L4
def error_family(client, verbose=True):
    """L4: malformed parameters -> error envelope (codes, language, stack style)."""
    if verbose:
        print(t("errf.header"))
    probes = [
        ("temperature_out_of_range", {"temperature": 2.0}, None, 1),
        ("temperature_wrong_type", {"temperature": "hot"}, None, 1),
        ("reasoning_effort_invalid", {"reasoning_effort": "none"}, None, 1),
        ("bad_role", None, [{"role": "wizard", "content": "hi"}], 1),
        ("developer_role_openai_style", None, [{"role": "developer", "content": "say hi"}], 5),
        ("huge_max_tokens", None, [{"role": "user", "content": "Count from 1 upward."}], 999999),
    ]
    out = {}
    for name, extra, messages, mx in probes:
        messages = messages or [{"role": "user", "content": "hi"}]
        d, e = client.chat(messages, max_tokens=mx, extra=extra)
        if e:
            out[name] = {"http": e["http"], "body": e["body"]}
            if verbose:
                print("%-30s => HTTP %s %s" % (name, e["http"], e["body"][:170]))
        else:
            out[name] = {"http": 200, "prompt_tokens": d["usage"]["prompt_tokens"],
                         "finish_reason": d["choices"][0]["finish_reason"]}
            if verbose:
                print("%-30s => 200 OK pt=%d finish=%s" % (
                    name, d["usage"]["prompt_tokens"], d["choices"][0]["finish_reason"]))
    return out


# ------------------------------------------------------------------ L6
def _image_message(b64, prompt="Is this image red or blue? Answer one word."):
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]


def vision_truth(client, colors=None, verbose=True):
    """L6: color ground truth + token-overhead slope across image sizes.
    Retries each probe (heterogeneous backends may 400 array content)."""
    if verbose:
        print(t("vision.header"))
        print(t("vision.hint"))
    colors = colors or [((255, 0, 0), "red"), ((0, 0, 255), "blue")]

    pt_noimg, _ = client.prompt_tokens(
        [{"role": "user", "content": "Is this image red or blue? Answer one word."}])
    if verbose:
        print(t("vision.control", pt=pt_noimg))

    out = {"no_image_control_prompt_tokens": pt_noimg, "probes": [], "failures": []}
    for size in [(64, 64), (256, 256)]:
        for rgb, color in colors:
            last_err = None
            for attempt in range(3):
                d, e = client.chat(_image_message(png_b64(size, rgb)), max_tokens=300)
                if e:
                    last_err = e
                    kind, _ = _classify_multimodal(e)
                    out["failures"].append({"size": "%dx%d" % size, "true_color": color,
                                            "attempt": attempt + 1, "kind": kind,
                                            "http": e.get("http"), "body": (_err_text(e) or "")[:200]})
                    continue
                m = d["choices"][0]["message"]
                row = {"size": "%dx%d" % size, "true_color": color,
                       "answer": (m.get("content") or "").strip(),
                       "prompt_tokens": d["usage"]["prompt_tokens"],
                       "delta": d["usage"]["prompt_tokens"] - pt_noimg, "attempts": attempt + 1}
                out["probes"].append(row)
                if verbose:
                    print("%-10s %-4s => %-8r (+%d tokens, tries=%d)" % (
                        row["size"], color, row["answer"], row["delta"], row["attempts"]))
                last_err = None
                break
            if last_err and verbose:
                print("%-10s %-4s => FAIL %s" % ("%dx%d" % size, color, _err_text(last_err)[:120]))
    correct = sum(1 for p in out["probes"] if p["true_color"] in p["answer"].lower())
    out["color_correct"] = "%d/%d" % (correct, len(out["probes"]))
    if verbose:
        print("color correct: %s" % out["color_correct"])
    return out


def vision_repeat(client, n=24, size=(64, 64), rgb=(255, 0, 0), max_tokens=48, verbose=True):
    """Repeated identical vision probe to quantify heterogeneous-backend mix."""
    if verbose:
        print(t("vision.repeat", n=n, size="%dx%d" % size))
    b64 = png_b64(size, rgb)
    counts = {}
    attempts = []
    for i in range(n):
        d, e = client.chat(_image_message(b64), max_tokens=max_tokens)
        if e:
            kind, detail = _classify_multimodal(e)
            pt = None
            ans = rea = ""
            http = e.get("http")
        else:
            m = d["choices"][0]["message"]
            ans = m.get("content") or ""
            rea = m.get("reasoning_content") or ""
            kind, detail = _classify_multimodal(None, ans, rea)
            pt = d["usage"]["prompt_tokens"]
            http = 200
        counts[kind] = counts.get(kind, 0) + 1
        row = {"i": i + 1, "kind": kind, "prompt_tokens": pt, "detail": (detail or "")[:160],
               "http": http, "answer": (ans or "")[:160]}
        attempts.append(row)
        if verbose:
            print("  #%02d/%d %-20s pt=%s %s" % (i + 1, n, kind, pt, (detail or "")[:90]))
    if verbose:
        print(t("vision.repeat_counts", counts=json.dumps(counts, ensure_ascii=False)))
    return {"n": n, "size": "%dx%d" % size, "counts": counts, "attempts": attempts}


def video_probe(client, path=None, verbose=True):
    """Try common video content-block shapes. Generates a tiny mp4 via ffmpeg if needed."""
    if verbose:
        print(t("video.header"))
    if path is None:
        import subprocess, tempfile
        path = os.path.join(tempfile.gettempdir(), "stealthprint-red16.mp4")
        if not os.path.exists(path) or os.path.getsize(path) < 100:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16:d=0.4:r=5",
                     "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", path],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.CalledProcessError) as e:
                raise RuntimeError(t("err.missing_dep", dep="ffmpeg", pkgs="ffmpeg")) from e
    raw = open(path, "rb").read()
    data_uri = "data:video/mp4;base64," + base64.b64encode(raw).decode()
    q = "Is this video mostly red or blue? One word."
    shapes = {
        "video_url_data": [
            {"type": "text", "text": q},
            {"type": "video_url", "video_url": {"url": data_uri}}],
        "video_data": [
            {"type": "text", "text": q},
            {"type": "video", "video": {"url": data_uri}}],
        "image_url_mp4_data": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {"url": data_uri}}],
    }
    out = {"bytes": len(raw), "shapes": {}}
    for name, content in shapes.items():
        d, e = client.chat([{"role": "user", "content": content}], max_tokens=64, timeout=90)
        if e:
            kind, detail = _classify_multimodal(e)
            pt = None
            http = e.get("http")
        else:
            m = d["choices"][0]["message"]
            kind, detail = _classify_multimodal(
                None, m.get("content") or "", m.get("reasoning_content") or "")
            pt = d["usage"]["prompt_tokens"]
            http = 200
        out["shapes"][name] = {"http": http, "kind": kind, "prompt_tokens": pt,
                               "detail": (detail or "")[:200]}
        if verbose:
            print("%-22s http=%s kind=%-20s pt=%s %s" % (
                name, http, kind, pt, (detail or "")[:90]))
    return out


def _sku_card(client, model, verbose=True):
    """Cheap same-gateway SKU card: wrapper, emoji delta, special-token, effort=none, one image."""
    base = "You are a helpful assistant. Repeat the following text exactly and add nothing else:\n\n"
    card = {"id": model}

    def pt(messages, extra=None):
        n, e = client.prompt_tokens(messages, extra=extra, model=model)
        return n, e

    hi, e = pt([{"role": "user", "content": "hi"}])
    card["hi_prompt_tokens"] = hi
    card["hi_error"] = None if not e else {"http": e.get("http"), "body": _err_text(e)[:200]}
    b, _e_base = pt([{"role": "user", "content": base}])
    be, _e_emoji = pt([{"role": "user", "content": base + EMOJI_ZWJ}])
    card["emoji_delta"] = None if (b is None or be is None) else be - b
    sp, _e_sp = pt([{"role": "user", "content": "hi" + SPECIAL_IMAGE}])
    card["special_image_delta"] = None if (hi is None or sp is None) else sp - hi
    d, e_none = client.chat([{"role": "user", "content": "hi"}], extra={"reasoning_effort": "none"},
                            model=model)
    if e_none:
        kind, detail = _classify_multimodal(e_none)
        card["effort_none"] = {"http": e_none.get("http"), "kind": kind, "detail": (detail or "")[:200]}
    else:
        card["effort_none"] = {"http": 200, "kind": "accepted",
                               "prompt_tokens": d["usage"]["prompt_tokens"]}
    d, e_img = client.chat(_image_message(png_b64((64, 64), (255, 0, 0))), max_tokens=128, model=model)
    if e_img:
        kind, detail = _classify_multimodal(e_img)
        card["vision"] = {"http": e_img.get("http"), "kind": kind, "detail": (detail or "")[:200]}
    else:
        m = d["choices"][0]["message"]
        kind, detail = _classify_multimodal(
            None, m.get("content") or "", m.get("reasoning_content") or "")
        card["vision"] = {"http": 200, "kind": kind, "prompt_tokens": d["usage"]["prompt_tokens"],
                          "detail": detail[:120]}
    if verbose:
        en = card["effort_none"]
        vis = card["vision"]
        print("%-16s hi=%s emojiΔ=%s specialΔ=%s none=%s/%s vis=%s/%s" % (
            model, hi, card["emoji_delta"], card["special_image_delta"],
            en.get("http"), en.get("kind"), vis.get("http"), vis.get("kind")))
    return card


def catalog_ab(client, peers=None, family=None, verbose=True):
    """Compare the target against named siblings on the same /v1/models catalog.

    peers: explicit id list. family: substring filter on catalog ids (e.g. 'glm').
    Always includes client.model. Wrapper offset is hi_pt(target) - hi_pt(peer).
    """
    ids, err = client.list_models()
    if err:
        raise RuntimeError("GET /models failed: %s" % err)
    if verbose:
        print(t("cat.header", n=len(ids)))
        print(t("cat.ids", ids=", ".join(ids[:40]) + ("…" if len(ids) > 40 else "")))
    want = []
    if peers:
        want.extend(p.strip() for p in peers if p.strip())
    if family:
        want.extend(i for i in ids if family.lower() in i.lower())
    seen = set()
    ordered = [client.model]
    for i in want:
        if i not in seen and i != client.model:
            seen.add(i)
            ordered.append(i)
    if verbose:
        print(t("cat.peers", peers=", ".join(ordered)))
    cards = []
    for mid in ordered:
        cards.append(_sku_card(client, mid, verbose=verbose))
    target = cards[0]
    for c in cards[1:]:
        if target.get("hi_prompt_tokens") is not None and c.get("hi_prompt_tokens") is not None:
            c["wrapper_offset_vs_target"] = target["hi_prompt_tokens"] - c["hi_prompt_tokens"]
    return {"catalog_ids": ids, "cards": cards}
