"""Gateway census (codified from the 2026-09-17 OpenCode free-tier survey).

Classify every catalog model by reachability first — a visible catalog entry
is not a usable model (62/71 entries were 401 credit-walled) — then run the
L1 tokenizer differential on the reachable ones and score against the local
vocab candidates. Models whose /chat/completions fails get one /responses
attempt (the Muse line only speaks the Responses API).
"""

import json
import os
import time

from .i18n import t, load_probes
from .probes_extra import encode_len, load_local_tokenizers

# 12-probe discriminating subset used by the 2026-09-17 census.
SURVEY_PROBES = ["zh_long", "emoji_zwj", "digits", "ja", "ko", "fr", "ru", "thai",
                 "code_indent", "url_sym", "cjk_rare", "mixed_code_zh"]


def classify_error(err):
    """Map a client error dict to a census class name."""
    http = (err or {}).get("http")
    body = str((err or {}).get("body", "")).lower()
    if http == 401 or "creditserror" in body:
        return "paid_401"
    if http == 429:
        return "rate_limited_429"
    if http in (500, 502, 503):
        return "upstream_5xx"
    if http == 400:
        return "bad_request_400"
    if http is None:
        return "timeout_or_network"
    return "other_http_%s" % http


def _pt_chat(client, text, retries, delay):
    err = {"http": None, "body": "not called"}
    for _ in range(retries + 1):
        pt, err = client.prompt_tokens([{"role": "user", "content": text}], max_tokens=16)
        if pt is not None:
            return pt, None
        if delay:
            time.sleep(delay)
    return None, err


def _pt_responses(client, model, text):
    d, err = client.request("POST", "/responses",
                            body={"model": model, "input": text, "max_output_tokens": 16})
    if err:
        return None, err
    usage = d.get("usage") or {}
    if "input_tokens" not in usage:
        return None, {"http": 200, "body": "no input_tokens in usage"}
    return usage["input_tokens"], None


def _score(deltas, local_deltas, base_pt, local_base):
    common = [p for p in deltas if p in local_deltas]
    if not common:
        return None
    exact = sum(1 for p in common if deltas[p] == local_deltas[p])
    mae = sum(abs(deltas[p] - local_deltas[p]) for p in common) / len(common)
    return {"exact": exact, "n": len(common), "mae": round(mae, 2),
            "wrapper": base_pt - local_base}


def _measure(client_factory, model, base_text, probe_texts, retries, delay):
    """Probe one model. Returns (row_or_None, class_or_None, error_detail)."""
    client = client_factory(model)
    base_pt, err = _pt_chat(client, base_text, retries, delay)
    mode = "chat"
    if base_pt is None:
        base_pt, err2 = _pt_responses(client, model, base_text)
        if base_pt is None:
            return None, classify_error(err), {"chat": err, "responses": err2}
        mode = "responses"

    deltas, fails = {}, 0
    for name, text in probe_texts:
        pt = None
        if mode == "chat":
            pt, _ = _pt_chat(client, text, retries, delay)
        else:
            for _ in range(retries + 1):
                pt, e = _pt_responses(client, model, text)
                if pt is not None:
                    break
                if delay:
                    time.sleep(delay)
        if pt is None:
            fails += 1
            continue
        deltas[name] = pt - base_pt
    return {"mode": mode, "base_api": base_pt, "deltas": deltas, "fails": fails}, None, None


def _done_models(out_path):
    done = set()
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["model"])
            except (ValueError, KeyError):
                continue
    return done


def run_survey(client_factory, models, tokenizers_dir="tok", probe_names=None,
               filter_sub=None, max_models=0, out_path=None, delay=0.5,
               retries=1, verbose=True):
    """Census the gateway catalog. client_factory(model_id) -> chat client;
    client_factory(None) is only used for catalog fetching by the CLI."""
    if probe_names is None:
        probe_names = SURVEY_PROBES
    pr = load_probes()
    base_text = pr["base"]
    probe_map = dict(pr["probes"])
    missing = [p for p in probe_names if p not in probe_map]
    if missing:
        raise ValueError("unknown probes: %s" % ", ".join(missing))
    probe_texts = [(p, base_text + probe_map[p]) for p in probe_names]

    local = load_local_tokenizers(tokenizers_dir)
    local_base, local_deltas = {}, {}
    for name, tk in local.items():
        b = encode_len(name, tk, base_text)
        local_base[name] = b
        local_deltas[name] = {p: encode_len(name, tk, text) - b for p, text in probe_texts}

    todo = [m for m in models if not filter_sub or filter_sub in m]
    if out_path and os.path.exists(out_path):
        done = _done_models(out_path)
        todo = [m for m in todo if m not in done]
        if done and verbose:
            print(t("survey.resume", n=len(done), path=out_path))
    if max_models > 0:
        todo = todo[:max_models]

    result = {"catalog_size": len(models), "surveyed": len(todo),
              "classes": {}, "measured": {}}
    for i, model in enumerate(todo, 1):
        row, cls, detail = _measure(client_factory, model, base_text,
                                    probe_texts, retries, delay)
        if row is None:
            result["classes"].setdefault(cls, []).append(model)
            if verbose:
                print(t("survey.model_class", model=model, cls=cls,
                        detail=str((detail or {}).get("chat"))[:60]))
        else:
            scores = {}
            for name in local:
                s = _score(row["deltas"], local_deltas[name],
                           row["base_api"], local_base[name])
                if s:
                    scores[name] = s
            row["best"] = max(scores, key=lambda k: (scores[k]["exact"], -scores[k]["mae"])) if scores else None
            row["scores"] = scores
            result["measured"][model] = row
            if verbose:
                s = scores.get(row["best"]) or {}
                print(t("survey.model_ok", model=model, mode=row["mode"],
                        base=row["base_api"], best=row["best"] or "-",
                        exact=s.get("exact", 0), n=s.get("n", 0), mae=s.get("mae", "-")))
        if out_path:
            payload = {"model": model, "class": cls}
            payload.update(row or {})
            if detail:
                payload["error_detail"] = detail
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if delay and i < len(todo):
            time.sleep(delay)

    if verbose:
        print(t("survey.summary", catalog=len(models),
                measured=len(result["measured"]),
                paid=len(result["classes"].get("paid_401", []))))
    return result
