"""Additional measurements using the same client and local token counting.

- tokenizer_repeats: count-stability-aware L1 (interleaved repeated measurements), reusing layers.tokenizer_differential for local diffs.
- echo_verify: L1b completion-side vocab check (model repeats probe text;
  API completion_tokens vs local tokenizer encode of the same text).
- tools_probe: L5 tool-calling behavior (schema overhead, tool_choice,
  real call, parallel calls).
- wrapper_turns: L2+ per-turn template accumulation curve.

All probes retain counting observations separately from identity interpretations.
"""

from collections import Counter

from . import layers
from .client import require_openai
from .layers import encode_len, load_local_tokenizers
from .i18n import t, load_probes


def _mode_and_dist(vals):
    ok = [v for v in vals if v is not None]
    if not ok:
        return None, {}
    return Counter(ok).most_common(1)[0][0], dict(sorted(Counter(ok).items()))


def tokenizer_repeats(client, probes=None, tokenizers_dir="tok", repeats=4, verbose=True):
    """Interleave repeated texts; rank only when all observed counts are stable.

    Count variation is an observation, not proof of multiple model backends.
    No cross-request path pairing is inferred from independent modes.
    """
    if repeats < 1:
        raise ValueError("repeats must be positive")
    probes = load_probes() if probes is None else probes
    local = load_local_tokenizers(tokenizers_dir)
    sources = layers.tokenizer_sources(tokenizers_dir, local)
    # Exercise local encoding before remote sampling.
    for name, tk in local.items():
        for text in [probes["base"]] + [probes["base"] + text for _, text in probes["probes"]]:
            encode_len(name, tk, text)
    texts = [("base", probes["base"])] + [(name, probes["base"] + text) for name, text in probes["probes"]]
    samples = {name: [] for name, _ in texts}
    failures = []
    for repeat in range(repeats):
        for name, text in texts:
            count, error = client.prompt_tokens([{"role": "user", "content": text}])
            samples[name].append(count)
            if error:
                failures.append({"probe": name, "repeat": repeat + 1, "error": error})
    distribution = {name: _mode_and_dist(values)[1] for name, values in samples.items()}
    varying = any(len(values) > 1 for values in distribution.values())
    result = {"distribution": distribution, "samples": samples, "failures": failures,
              "count_variation": varying, "api_delta": {}, "ranking": {}, "wrapper": {},
              "probes": probes, "tokenizers": sources}
    if varying or any(value is None for values in samples.values() for value in values):
        result["status"] = "unstable_counts" if varying else "incomplete"
        if verbose:
            print(t("xrep.mixed"))
            print(distribution)
        return result
    base = samples["base"][0]
    delta = {name: values[0] - base for name, values in samples.items() if name != "base"}
    result.update(layers.tokenizer_differential(
        client, probes=probes, tokenizers_dir=tokenizers_dir, verbose=verbose,
        base_api=base, api_delta=delta, local=local))
    result["failures"] = failures
    result["status"] = "stable_sample"
    return result


_ECHO_PROMPT = "Repeat the following text exactly, character for character, with no commentary:\n\n"


def echo_verify(client, probes=None, tokenizers_dir="tok", verbose=True):
    """L1b: completion-side vocab check. The model echoes a probe; API
    completion_tokens is compared against local tokenizations of the echoed
    visible text. Offsets are observations, not inferred hidden reasoning."""
    require_openai(client)
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
        echoed_ok = content == text
        row = {"completion_tokens": comp, "echo_exact": echoed_ok,
               "content": content, "finish_reason": d["choices"][0].get("finish_reason"),
               "completion_tokens_details": d["usage"].get("completion_tokens_details")}
        row["offsets"] = {n: comp - encode_len(n, tk, content) for n, tk in local.items()}
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
    require_openai(client)
    out = {}
    plain, err = client.prompt_tokens([{"role": "user", "content": "hi"}])
    if err:
        out["baseline_error"] = err
    with_tools, tools_error = client.chat([{"role": "user", "content": "hi"}],
                                max_tokens=1, extra={"tools": _TOOL_SCHEMA})
    if tools_error:
        out["schema_error"] = tools_error
    if plain is not None and with_tools and (with_tools.get("usage") or {}).get("prompt_tokens") is not None:
        pt_tools = with_tools["usage"]["prompt_tokens"]
        out["schema_overhead"] = pt_tools - plain
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
    delta includes the same message bodies each turn, plus chat-template
    turn overhead (role headers + eot)."""
    out = {"turns": []}
    messages = []
    prev = None
    for i in range(turns):
        messages.append({"role": "user", "content": "Say OK."})
        pt, err = client.prompt_tokens(messages, max_tokens=1)
        row = {"turn": i + 1, "prompt_tokens": pt,
               "delta": (pt - prev) if (pt is not None and prev is not None) else None}
        if err:
            row["error"] = err
        out["turns"].append(row)
        if verbose:
            print("  turn %d: pt=%s delta=%s" % (i + 1, pt, row["delta"]))
        if pt is None:
            break
        prev = pt
        messages.append({"role": "assistant", "content": "OK"})
    deltas = [r["delta"] for r in out["turns"] if r["delta"] is not None]
    out["per_turn_constant"] = len(set(deltas)) == 1 if len(deltas) >= 2 else None
    return out
