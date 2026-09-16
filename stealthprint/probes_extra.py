"""Extra fingerprint probes beyond layers.py.

- tokenizer_repeats: mixed-backend aware L1 (repeat -> cluster -> pair within
  the majority path), reusing layers.tokenizer_differential for local diffs.
- echo_verify: L1b completion-side vocab check (model repeats probe text;
  API completion_tokens vs local tokenizer encode of the same text).
- tools_probe: L5 tool-calling behavior (schema overhead, tool_choice,
  real call, parallel calls).
- wrapper_turns: L2+ per-turn template accumulation curve.

These live in their own module to keep layers.py stable and to avoid
colliding with concurrent edits there.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import layers
from .i18n import t, load_probes


def _pt(client, text, retries=2):
    """prompt_tokens with light retry; None on persistent failure."""
    for _ in range(retries + 1):
        d, err = client.chat([{"role": "user", "content": text}], max_tokens=1)
        if d and isinstance(d.get("usage"), dict) and "prompt_tokens" in d["usage"]:
            return d["usage"]["prompt_tokens"]
    return None


def _mode_and_dist(vals):
    ok = [v for v in vals if v is not None]
    if not ok:
        return None, {}
    return Counter(ok).most_common(1)[0][0], dict(sorted(Counter(ok).items()))


def tokenizer_repeats(client, probes=None, tokenizers_dir="tok", repeats=4,
                      workers=8, verbose=True):
    """L1 with mixed-backend handling: sample every text N times, take the
    majority value per text, pair deltas within that path, then reuse
    layers.tokenizer_differential for the local comparison."""
    if probes is None:
        probes = load_probes()
    base = probes["base"]
    texts = {"base": base}
    for name, text in probes["probes"]:
        texts[name] = base + text

    jobs = [(k, t) for k, t in texts.items() for _ in range(repeats)]
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_pt, client, t): k for k, t in jobs}
        sampled = {}
        for f, k in futs.items():
            sampled.setdefault(k, []).append(f.result())

    distribution, modes, mixed = {}, {}, False
    for k, vals in sampled.items():
        mode, dist = _mode_and_dist(vals)
        modes[k] = mode
        distribution[k] = dist
        mixed = mixed or len(dist) > 1
        if verbose:
            print("  %-14s n=%d dist=%s mode=%s" % (k, len(dist) and repeats, dist, mode))

    if modes.get("base") is None:
        raise RuntimeError("base sampling failed entirely: %s" % distribution.get("base"))
    api_delta = {k: modes[k] - modes["base"] for k, v in modes.items()
                 if k != "base" and v is not None}
    result = layers.tokenizer_differential(
        client, probes=probes, tokenizers_dir=tokenizers_dir, verbose=verbose,
        base_api=modes["base"], api_delta=api_delta)
    result["distribution"] = distribution
    result["mixed_backends"] = mixed
    result["base_mode"] = modes["base"]
    if verbose and mixed:
        print(t("xrep.mixed"))
    return result


_ECHO_PROMPT = "Repeat the following text exactly, character for character, with no commentary:\n\n"


def encode_len(name, tk, s):
    """Token count for tokenizer name/obj pair (*_base are tiktoken, rest HF)."""
    if name.endswith("_base"):
        return len(tk.encode(s))
    return len(tk.encode(s, add_special_tokens=False).ids)


def load_local_tokenizers(tokenizers_dir="tok"):
    import os
    try:
        from tokenizers import Tokenizer
        import tiktoken
    except ImportError as e:
        raise RuntimeError(t("err.missing_dep", dep=e.name, pkgs="tokenizers tiktoken")) from e
    local = {}
    if os.path.isdir(tokenizers_dir):
        for f in sorted(os.listdir(tokenizers_dir)):
            if f.endswith(".json"):
                local[f[:-5]] = Tokenizer.from_file(os.path.join(tokenizers_dir, f))
    local["o200k_base"] = tiktoken.get_encoding("o200k_base")
    local["cl100k_base"] = tiktoken.get_encoding("cl100k_base")
    return local


def echo_verify(client, probes=None, tokenizers_dir="tok", verbose=True):
    """L1b: completion-side vocab check. The model echoes a probe; API
    completion_tokens is compared against local tokenizations of the echoed
    text. A constant offset per vocab = generation-side template tail."""
    if probes is None:
        probes = load_probes()
    local = load_local_tokenizers(tokenizers_dir)

    out = {"echoes": {}}
    for name, text in probes["probes"][:8]:
        d, err = client.chat([{"role": "user", "content": _ECHO_PROMPT + text}],
                             max_tokens=max(64, 4 * len(text)))
        if err or not (d.get("choices") and d.get("usage", {}).get("completion_tokens") is not None):
            out["echoes"][name] = {"error": str(err or d)[:160]}
            continue
        content = (d["choices"][0].get("message") or {}).get("content") or ""
        comp = d["usage"]["completion_tokens"]
        echoed_ok = content.strip() == text
        row = {"completion_tokens": comp, "echo_exact": echoed_ok,
               "content_prefix": content.strip()[:24]}
        if echoed_ok:
            row["offsets"] = {n: comp - encode_len(n, tk, text) for n, tk in local.items()}
        out["echoes"][name] = row
        if verbose:
            extra = " offsets=%s" % row["offsets"] if echoed_ok else ""
            print("  %-14s comp=%-4d exact=%s%s" % (name, comp, echoed_ok, extra))
    return out


_TOOL_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string"}}, "required": ["city"]},
    }},
    {"type": "function",
     "function": {
        "name": "get_time",
        "description": "Get current time in a timezone",
        "parameters": {"type": "object", "properties": {
            "timezone": {"type": "string"}}, "required": ["timezone"]},
    }},
]


def tools_probe(client, verbose=True):
    """L5: schema token overhead, tool_choice handling, real/parallel calls."""
    out = {}
    plain, err = client.prompt_tokens([{"role": "user", "content": "hi"}])
    with_tools, _ = client.chat([{"role": "user", "content": "hi"}],
                                max_tokens=1, extra={"tools": _TOOL_SCHEMA})
    if with_tools and (with_tools.get("usage") or {}).get("prompt_tokens") is not None:
        pt_tools = with_tools["usage"]["prompt_tokens"]
        out["schema_overhead"] = pt_tools - (plain if plain is not None else 0)
    else:
        out["schema_overhead"] = None

    for choice in ("none", "auto", "required"):
        d, e = client.chat([{"role": "user", "content": "What's the weather in Paris and the time in UTC?"}],
                           max_tokens=200, extra={"tools": _TOOL_SCHEMA, "tool_choice": choice})
        if e or not d.get("choices"):
            out["tool_choice_" + choice] = {"error": str(e or d)[:160]}
            continue
        msg = d["choices"][0].get("message") or {}
        calls = msg.get("tool_calls") or []
        row = {"finish": d["choices"][0].get("finish_reason"), "n_calls": len(calls)}
        if calls:
            row["calls"] = [{"name": (c.get("function") or {}).get("name"),
                             "args": (c.get("function") or {}).get("arguments")} for c in calls]
        out["tool_choice_" + choice] = row
        if verbose:
            print("  tool_choice=%-8s finish=%-9s calls=%s" % (
                choice, row.get("finish"), row.get("calls") or row.get("n_calls")))
    return out


def wrapper_turns(client, turns=4, verbose=True):
    """L2+: prompt_tokens as user/assistant turns accumulate. The per-turn
    delta is the chat-template turn overhead (role headers + eot)."""
    out = {"turns": []}
    messages = []
    prev = None
    for i in range(turns):
        messages.append({"role": "user", "content": "Say OK." if i % 2 == 0 else "OK"})
        pt, err = client.prompt_tokens(messages, max_tokens=1)
        row = {"turn": i + 1, "prompt_tokens": pt,
               "delta": (pt - prev) if (pt is not None and prev is not None) else None}
        out["turns"].append(row)
        if verbose:
            print("  turn %d: pt=%s delta=%s" % (i + 1, pt, row["delta"]))
        if pt is None:
            break
        prev = pt
        messages.append({"role": "assistant", "content": "OK"})
    deltas = [r["delta"] for r in out["turns"] if r["delta"] is not None]
    out["per_turn_constant"] = len(set(deltas[1:])) == 1 if len(deltas) > 2 else None
    return out
