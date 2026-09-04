"""Fingerprint layers. Each returns a plain dict (stable English keys) and
prints a human-readable summary via i18n.t()."""

from .i18n import t

FILL = "The quiet harbor town woke slowly under a gray sky, and fishermen checked their nets. "


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

    import os
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
def vision_truth(client, colors=None, verbose=True):
    """L6: color ground truth + token-overhead slope across image sizes.
    Retries each probe (heterogeneous backends may 400 array content)."""
    try:
        import base64, io
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(t("err.missing_dep", dep=e.name, pkgs="pillow")) from e

    if verbose:
        print(t("vision.header"))
        print(t("vision.hint"))
    colors = colors or [((255, 0, 0), "red"), ((0, 0, 255), "blue")]

    def png_b64(size, rgb):
        buf = io.BytesIO()
        Image.new("RGB", size, rgb).save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode()

    pt_noimg, _ = client.prompt_tokens(
        [{"role": "user", "content": "Is this image red or blue? Answer one word."}])
    if verbose:
        print(t("vision.control", pt=pt_noimg))

    out = {"no_image_control_prompt_tokens": pt_noimg, "probes": []}
    for size in [(64, 64), (256, 256)]:
        for rgb, color in colors:
            for attempt in range(3):
                d, e = client.chat([{"role": "user", "content": [
                    {"type": "text", "text": "Is this image red or blue? Answer one word."},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + png_b64(size, rgb)}}]}], max_tokens=300)
                if e:
                    continue
                m = d["choices"][0]["message"]
                row = {"size": "%dx%d" % size, "true_color": color, "answer": (m.get("content") or "").strip(),
                       "prompt_tokens": d["usage"]["prompt_tokens"],
                       "delta": d["usage"]["prompt_tokens"] - pt_noimg, "attempts": attempt + 1}
                out["probes"].append(row)
                if verbose:
                    print("%-10s %-4s => %-8r (+%d tokens, tries=%d)" % (
                        row["size"], color, row["answer"], row["delta"], row["attempts"]))
                break
    correct = sum(1 for p in out["probes"] if p["true_color"] in p["answer"].lower())
    out["color_correct"] = "%d/%d" % (correct, len(out["probes"]))
    if verbose:
        print("color correct: %s" % out["color_correct"])
    return out
