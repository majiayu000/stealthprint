#!/usr/bin/env python3
"""omenprint — stealth-model fingerprinting toolkit.

Layers (run independently):
  tokenizer  L1  prompt_tokens differential vs local tokenizer.json files
  wrapper    L2  gateway/template constant overhead
  context    L3  binary-search context limit + needle-in-haystack retrieval
  errors     L4  malformed-parameter error envelope family
  vision     L6  image token overhead + color/dimension ground-truth test

Usage:
  export FINGERPRINT_BASE_URL="https://opencode.ai/zen/go/v1"
  export FINGERPRINT_MODEL="omen-alpha"
  export FINGERPRINT_API_KEY="..."        # or --api-key; also reads opencode auth.json

  python3 omenprint.py tokenizer --probes probes.json --tokenizers tok/
  python3 omenprint.py wrapper
  python3 omenprint.py context --max-bytes 4500000
  python3 omenprint.py errors
  python3 omenprint.py vision
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "omen-alpha"
HERE = os.path.dirname(os.path.abspath(__file__))


def cfg():
    base = os.environ.get("FINGERPRINT_BASE_URL", DEFAULT_BASE).rstrip("/")
    model = os.environ.get("FINGERPRINT_MODEL", DEFAULT_MODEL)
    key = os.environ.get("FINGERPRINT_API_KEY")
    if not key:
        p = os.path.expanduser("~/.local/share/opencode/auth.json")
        if os.path.exists(p):
            d = json.load(open(p))
            for provider in ("opencode-go", "opencode"):
                if provider in d and isinstance(d[provider], dict) and "key" in d[provider]:
                    key = d[provider]["key"]
                    break
    if not key:
        sys.exit("error: no API key (set FINGERPRINT_API_KEY)")
    return base, model, key


def chat(messages, max_tokens=1, extra=None, timeout=180):
    base, model, key = cfg()
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if extra:
        body.update(extra)
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": "curl/8.7.1", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, {"http": e.code, "body": e.read().decode()[:600]}
    except Exception as e:
        return None, {"http": None, "body": str(e)}


def prompt_tokens(messages, max_tokens=1, extra=None):
    d, err = chat(messages, max_tokens, extra)
    if err:
        return None, err
    return d["usage"]["prompt_tokens"], None


# ---------------------------------------------------------------- layer 1/2
def cmd_tokenizer(args):
    try:
        from tokenizers import Tokenizer
        import tiktoken
    except ImportError:
        sys.exit("error: pip install tokenizers tiktoken")

    probes = json.load(open(args.probes))
    base = probes["base"]
    items = probes["probes"]

    print("== querying target (%d probes) ==" % len(items))
    base_api, err = prompt_tokens([{"role": "user", "content": base}])
    if err:
        sys.exit("error: base request failed: %s" % err)
    api_delta = {}
    for name, text in items:
        pt, e = prompt_tokens([{"role": "user", "content": base + text}])
        if e:
            print("  %s: FAILED %s" % (name, e))
            continue
        api_delta[name] = pt - base_api
        print("  %-12s prompt=%-5d delta=%d" % (name, pt, pt - base_api))

    locals_ = {}
    if os.path.isdir(args.tokenizers):
        for f in sorted(os.listdir(args.tokenizers)):
            if f.endswith(".json"):
                try:
                    locals_[f[:-5]] = Tokenizer.from_file(os.path.join(args.tokenizers, f))
                except Exception as e:
                    print("skip %s: %s" % (f, e))
    locals_["o200k_base"] = tiktoken.get_encoding("o200k_base")

    def ntok(name, tk, s):
        if name == "o200k_base":
            return len(tk.encode(s))
        return len(tk.encode(s, add_special_tokens=False).ids)

    print("\n%-14s | %s | API" % ("probe", " ".join("%-8s" % n[:8] for n in locals_)))
    for name, text in items:
        if name not in api_delta:
            continue
        vals = [ntok(n, tk, text) for n, tk in locals_.items()]
        print("%-14s | %s | %d" % (name, " ".join("%-8d" % v for v in vals), api_delta[name]))

    print("\n== ranking ==")
    names = list(locals_)
    targets = [api_delta[n] for n, _ in items if n in api_delta]
    for n in names:
        vals = [ntok(n, locals_[n], t) for name, t in items if name in api_delta]
        exact = sum(1 for v, d in zip(vals, targets) if v == d)
        mae = sum(abs(v - d) for v, d in zip(vals, targets)) / len(targets)
        mark = "  <== MATCH" if exact == len(targets) else ""
        print("%-14s exact=%2d/%d mae=%.2f%s" % (n, exact, len(targets), mae, mark))

    print("\n== wrapper constant (api - raw) ==")
    for n in names:
        raw_base = ntok(n, locals_[n], base)
        print("%-14s wrapper=%d" % (n, base_api - raw_base))


def cmd_wrapper(args):
    print("== wrapper constant across lengths ==")
    texts = ["hi", "hello", "Say OK.", "Please answer with a single word: yes or no, thanks."]
    prev = None
    for t in texts:
        pt, e = prompt_tokens([{"role": "user", "content": t}])
        if e:
            print("FAIL", t, e)
            continue
        print("%-52r prompt_tokens=%d" % (t, pt))
        prev = pt
    print("constant delta across lengths => fixed chat template; "
          "compare against each candidate tokenizer's raw token count.")


# ---------------------------------------------------------------- layer 3
FILL = "The quiet harbor town woke slowly under a gray sky, and fishermen checked their nets. "


def _fill_prompt(chars, task):
    return task + "\n\n" + FILL * (chars // len(FILL))


def cmd_context(args):
    task = "Answer with the single word OK and nothing else."

    def ok_at(chars):
        pt, e = prompt_tokens([{"role": "user", "content": _fill_prompt(chars, task)}])
        return (True, pt, None) if e is None else (False, None, e)

    print("== context limit binary search (bounded by gateway body limit) ==")
    lo, hi = 10_000, args.max_bytes
    while hi - lo > 30_000:
        mid = (lo + hi) // 2
        good, pt, e = ok_at(mid)
        print("  %,d chars: %s" % (mid, ("OK pt=" + format(pt, ",")) if good else "FAIL " + json.dumps(e)[:160]), flush=True)
        if good:
            lo = mid
        else:
            hi = mid
    good, pt, _ = ok_at(lo)
    print("max verified: %,d chars => %,d prompt_tokens" % (lo, pt or 0))

    print("\n== needle-in-haystack (head/mid/tail) ==")
    units = int(lo * args.needle_ratio / len(FILL))
    needles = {units // 20: ("ALPHA", "48219"), units // 2: ("BRAVO", "70553"), units * 19 // 20: ("CHARLIE", "91024")}
    parts = []
    for u in range(units):
        if u in needles:
            lab, code = needles[u]
            parts.append("\nREMEMBER THIS EXACTLY: the vault code at checkpoint %s is %s. \n" % (lab, code))
        else:
            parts.append(FILL)
    q = ("Each of the three vault codes below is hidden in the text. Reply with only the "
         "three codes in order, comma separated.\n\n" + "".join(parts))
    d, e = chat([{"role": "user", "content": q}], max_tokens=args.needle_max_tokens, timeout=600)
    if e:
        print("needle FAILED:", e)
        return
    m = d["choices"][0]["message"]
    print("prompt_tokens=%d finish=%s" % (d["usage"]["prompt_tokens"], d["choices"][0]["finish_reason"]))
    print("answer:", repr(m.get("content")))
    print("expected: 48219, 70553, 91024")


# ---------------------------------------------------------------- layer 4
def cmd_errors(args):
    probes = [
        ("temperature out of range", {"temperature": 2.0}, None, 1),
        ("temperature wrong type", {"temperature": "hot"}, None, 1),
        ("reasoning_effort invalid", {"reasoning_effort": "none"}, None, 1),
        ("bad role", None, [{"role": "wizard", "content": "hi"}], 1),
        ("developer role (OpenAI-style)", None, [{"role": "developer", "content": "say hi"}], 5),
        ("huge max_tokens", None, [{"role": "user", "content": "Count from 1 upward."}], 999999),
    ]
    for name, extra, messages, mx in probes:
        messages = messages or [{"role": "user", "content": "hi"}]
        d, e = chat(messages, max_tokens=mx, extra=extra)
        if e:
            print("%-32s => HTTP %s %s" % (name, e["http"], e["body"][:180]))
        else:
            u = d["usage"]
            print("%-32s => 200 OK pt=%d finish=%s" % (name, u["prompt_tokens"], d["choices"][0]["finish_reason"]))


# ---------------------------------------------------------------- layer 6
RED_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def _png_b64(color, size):
    try:
        from PIL import Image
    except ImportError:
        sys.exit("error: pip install pillow")
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def cmd_vision(args):
    print("== vision ground-truth probes ==")
    print("constant +delta regardless of image size => placeholder, not a real encoder\n")
    pt_noimg, _ = prompt_tokens([{"role": "user", "content": "Is this image red or blue? Answer one word."}])
    print("no-image control pt=%d" % pt_noimg)
    for label, size, color in [("64x64 red", (64, 64), (255, 0, 0)), ("64x64 blue", (64, 64), (0, 0, 255)),
                               ("256x256 red", (256, 256), (255, 0, 0))]:
        for attempt in range(3):
            d, e = chat([{"role": "user", "content": [
                {"type": "text", "text": "Is this image red or blue? Answer one word."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png_b64(color, size)}}]}], max_tokens=300)
            if e:
                print("%-12s attempt %d => HTTP %s %s" % (label, attempt, e["http"], e["body"][:140]))
                continue
            m = d["choices"][0]["message"]
            print("%-12s pt=%d (+%d) content=%r reasoning=%r" % (
                label, d["usage"]["prompt_tokens"], d["usage"]["prompt_tokens"] - pt_noimg,
                m.get("content"), (m.get("reasoning_content") or "")[:110]))
            break


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tokenizer", help="L1 tokenizer differential")
    t.add_argument("--probes", default=os.path.join(HERE, "probes.json"))
    t.add_argument("--tokenizers", default=os.path.join(HERE, "tok"))
    sub.add_parser("wrapper", help="L2 template overhead constant")
    c = sub.add_parser("context", help="L3 context limit + needle retrieval")
    c.add_argument("--max-bytes", type=int, default=4_500_000, help="gateway body limit upper bound")
    c.add_argument("--needle-ratio", type=float, default=0.95, help="needle test size as fraction of verified max")
    c.add_argument("--needle-max-tokens", type=int, default=3000)
    sub.add_parser("errors", help="L4 error envelope family")
    sub.add_parser("vision", help="L6 vision ground truth")
    args = ap.parse_args()
    {"tokenizer": cmd_tokenizer, "wrapper": cmd_wrapper, "context": cmd_context,
     "errors": cmd_errors, "vision": cmd_vision}[args.cmd](args)


if __name__ == "__main__":
    main()
