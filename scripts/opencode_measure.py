"""Measure L1 token deltas through the real opencode CLI.

Each probe is one `opencode run --format json` call with a tool-less agent in an
empty directory. The prompt-token count is input + cache.read + cache.write of
the first step_finish event; the fixed opencode system prompt cancels in
delta = T(base+probe) - T(base). opencode 1.19 stores a message argument that
contains spaces as "<text>", and the closing quote can merge with the last
character, so local counts use the same wrapping (QUOTE). The base is measured
before and after the probes, and the row is marked incomplete when they differ.

Rows match `stealthprint survey` JSONL, so scripts/blind_eval.py can score them.

    python scripts/opencode_measure.py --truth eval/blind_truth_opencode_go.json --out go.jsonl
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from stealthprint.i18n import load_probes  # noqa: E402
from stealthprint.layers import encode_len, load_local_tokenizers, tokenizer_sources  # noqa: E402
from stealthprint.survey import SURVEY_PROBES, _score  # noqa: E402

AGENT_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "agent": {"probe": {"mode": "primary", "description": "token-count probe, no tools",
                        "prompt": "Reply with the single word: ok", "tools": {"*": False}}},
}


# How opencode run passes a spaced message argument to the model.
QUOTE = '"%s"'


def opencode_version():
    try:
        return subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def prompt_tokens(model, text, config, workdir, timeout=300):
    """(count, error) for one opencode run."""
    env = dict(os.environ, OPENCODE_CONFIG=config)
    try:
        proc = subprocess.run(["opencode", "run", "--pure", "--agent", "probe", "-m", model,
                               "--format", "json", "--dir", workdir, text], cwd=workdir, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "error":
            return None, json.dumps(event.get("error"))[:300]
        if event.get("type") == "step_finish":
            tokens = event["part"].get("tokens") or {}
            cache = tokens.get("cache") or {}
            total = (tokens.get("input") or 0) + (cache.get("read") or 0) + (cache.get("write") or 0)
            return (total, None) if total > 0 else (None, "no input tokens reported")
    return None, (proc.stderr.strip()[-300:] or "no step_finish event (exit %d)" % proc.returncode)


def measure(model, base, probes, config, workroot, retries=1):
    workdir = tempfile.mkdtemp(prefix="sp-oc-", dir=workroot)

    def count(text):
        error = None
        for _ in range(retries + 1):
            value, error = prompt_tokens(model, text, config, workdir)
            if value is not None:
                return value, None
        return None, error

    base_before, error = count(base)
    if base_before is None:
        return {"model": model, "class": "error", "complete": False, "error_detail": error}
    deltas, errors = {}, {}
    for name, text in probes:
        value, err = count(base + text)
        if value is None:
            errors[name] = err
        else:
            deltas[name] = value - base_before
    base_after, _ = count(base)
    complete = not errors and base_after == base_before
    return {"model": model, "class": None, "mode": "opencode-cli", "base_api": base_before,
            "base_api_after": base_after, "deltas": deltas, "fails": len(errors), "errors": errors,
            "complete": complete}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--truth", required=True)
    ap.add_argument("--provider", default="opencode-go")
    ap.add_argument("--tokenizers", default="tok")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default=None, help="comma-separated model ids")
    ap.add_argument("--workroot", default=None, help="parent for empty run directories; keep it outside git repos")
    args = ap.parse_args(argv)

    truth = json.load(open(args.truth, encoding="utf-8"))
    models = sorted(truth["models"]) + [m for m in truth.get("observe", []) if m not in truth["models"]]
    if args.only:
        models = [m for m in models if m in set(args.only.split(","))]
    done = set()
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as stream:
            done = {json.loads(l)["model"] for l in stream if l.strip() and json.loads(l).get("complete")}
    todo = [m for m in models if m not in done]

    pr = load_probes()
    base = pr["base"]
    probe_map = dict(pr["probes"])
    probes = [(p, probe_map[p]) for p in SURVEY_PROBES]
    local = load_local_tokenizers(args.tokenizers)
    local_base = {n: encode_len(n, tk, QUOTE % base) for n, tk in local.items()}
    local_deltas = {n: {p: encode_len(n, tk, QUOTE % (base + t)) - local_base[n] for p, t in probes}
                    for n, tk in local.items()}

    config = os.path.join(tempfile.mkdtemp(prefix="sp-oc-config-"), "opencode.json")
    with open(config, "w", encoding="utf-8") as stream:
        json.dump(AGENT_CONFIG, stream)
    experiment = {"client": "opencode-cli", "opencode": opencode_version(), "wrap": QUOTE,
                  "provider": args.provider, "probes": SURVEY_PROBES,
                  "tokenizers": tokenizer_sources(args.tokenizers, local)}

    def job(model):
        row = measure("%s/%s" % (args.provider, model), base, probes, config, args.workroot)
        row["model"] = model
        if row.get("deltas"):
            scores = {n: s for n in local
                      if (s := _score(row["deltas"], local_deltas[n], row["base_api"], local_base[n]))}
            row["scores"] = scores
            row["best"] = max(scores, key=lambda k: (scores[k]["exact"], -scores[k]["mae"])) if scores else None
        row["experiment"] = experiment
        row["measured_at"] = datetime.now(timezone.utc).isoformat()
        with open(args.out, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        best = row.get("best")
        s = (row.get("scores") or {}).get(best) or {}
        print("%-28s complete=%s best=%s %s/%s base=%s/%s" % (model, row["complete"], best, s.get("exact"),
              s.get("n"), row.get("base_api"), row.get("base_api_after")), flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(job, todo))


if __name__ == "__main__":
    main()
