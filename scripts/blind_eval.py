"""Blind accuracy test for the L1 tokenizer verdict.

run:   probe every model in eval/blind_truth.json through `survey` (billable,
       roughly 25 short requests per model) and append rows to a JSONL file.
score: read that JSONL and grade each verdict against the truth file. The
       verdict is computed from count deltas alone; model names never reach
       the scorer, so the test is blind by construction.

    python scripts/blind_eval.py run --base-url https://openrouter.ai/api/v1 --out blind.jsonl
    python scripts/blind_eval.py score blind.jsonl
"""
import argparse
import json
import os
import sys

TRUTH = os.path.join(os.path.dirname(__file__), "..", "eval", "blind_truth.json")


def verdict(row, high=1.0, medium=0.8):
    """(candidate or None, tier) from a survey row's L1 scores."""
    best = row.get("best")
    score = (row.get("scores") or {}).get(best) if best else None
    if not score or not score.get("n"):
        return None, "none"
    ratio = score["exact"] / score["n"]
    if ratio >= high:
        return best, "high"
    if ratio >= medium:
        return best, "medium"
    return None, "none"


def same(a, b, groups):
    return a == b or any(a in g and b in g for g in groups)


def score_rows(rows, truth):
    groups = [set(g) for g in truth.get("equivalent", [])]
    expected = truth["models"]
    graded = []
    for row in rows:
        model = row.get("model")
        if model not in expected or not row.get("complete"):
            continue
        guess, tier = verdict(row)
        want = expected[model]
        if want is None:
            outcome = "false_positive" if guess else "correct_reject"
        elif guess is None:
            outcome = "miss"
        else:
            outcome = "correct" if same(guess, want, groups) else "wrong"
        graded.append({"model": model, "expected": want, "verdict": guess, "tier": tier, "outcome": outcome})

    def rate(items, good):
        return round(sum(i["outcome"] in good for i in items) / len(items), 3) if items else None

    positives = [g for g in graded if g["expected"] is not None]
    negatives = [g for g in graded if g["expected"] is None]
    claimed = [g for g in graded if g["verdict"]]
    by_tier = {}
    for tier in ("high", "medium"):
        items = [g for g in claimed if g["tier"] == tier]
        by_tier[tier] = {"n": len(items), "precision": rate(items, {"correct"})}
    return {
        "graded": len(graded), "missing": sorted(set(expected) - {g["model"] for g in graded}),
        "positive_accuracy": rate(positives, {"correct"}),
        "negative_reject_rate": rate(negatives, {"correct_reject"}),
        "precision_by_tier": by_tier,
        "rows": graded,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--base-url", default=os.environ.get("STEALTHPRINT_BASE_URL"))
    p.add_argument("--api-key", default=os.environ.get("STEALTHPRINT_API_KEY"))
    p.add_argument("--tokenizers", default="tok")
    p.add_argument("--out", required=True)
    p.add_argument("--truth", default=TRUTH)
    p = sub.add_parser("score")
    p.add_argument("jsonl")
    p.add_argument("--truth", default=TRUTH)
    args = ap.parse_args(argv)
    truth = json.load(open(args.truth, encoding="utf-8"))

    if args.cmd == "run":
        if not args.base_url or not args.api_key:
            sys.exit("run needs --base-url and --api-key (or STEALTHPRINT_BASE_URL / STEALTHPRINT_API_KEY)")
        from stealthprint.client import ChatClient
        from stealthprint import survey

        def factory(model):
            return ChatClient(model=model or "probe", base_url=args.base_url, api_key=args.api_key)

        survey.run_survey(factory, sorted(truth["models"]), tokenizers_dir=args.tokenizers, out_path=args.out)
        return
    with open(args.jsonl, encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    print(json.dumps(score_rows(rows, truth), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
