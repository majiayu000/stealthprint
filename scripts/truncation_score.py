#!/usr/bin/env python3
"""Score truncation cuts against local tokenizers, independent of usage counts.

A reply cut by max_tokens ends on a token boundary however much hidden reasoning used,
and the tokens before the cut cannot exceed max_tokens. A candidate passes only if every
cut is a boundary and no cut is over budget. Passing is not unique proof: others may share it.

  python scripts/truncation_score.py docs/case-crouton-truncation.json <tokenizers_dir>
"""
import json
import sys

from stealthprint.layers import load_local_tokenizers


def visible_lengths(name, tk, text):
    """Visible chars after each token; a partial multibyte token shows nothing until complete."""
    if name in ("o200k_base", "cl100k_base"):
        out, seen = [], b""
        for piece in tk.decode_tokens_bytes(tk.encode(text, disallowed_special=())):
            seen += piece
            out.append(len(seen.decode("utf-8", errors="ignore")))
        return out
    ids = tk.encode(text, add_special_tokens=False).ids
    return [len(tk.decode(ids[:k], skip_special_tokens=False).rstrip("�")) for k in range(1, len(ids) + 1)]


def score(rows, lengths):
    cuts, boundaries = {c for _, c in rows}, set(lengths)
    over = sum(1 for n, c in rows if next((i + 1 for i, x in enumerate(lengths) if x >= c), 0) > n)
    return {"hit": len(cuts & boundaries), "cuts": len(cuts), "over": over}


def main():
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    local = load_local_tokenizers(sys.argv[2])
    lengths = {t: {n: visible_lengths(n, tk, text) for n, tk in local.items()} for t, text in data["texts"].items()}
    for text_id, models in data["observations"].items():
        for model, rows in models.items():
            results = {n: score(rows, lengths[text_id][n]) for n in local}
            passing = [n for n, r in results.items() if r["hit"] == r["cuts"] and r["over"] == 0]
            print("%s %-26s cuts=%-3d passing=%s" % (text_id, model, len({c for _, c in rows}), passing))


if __name__ == "__main__":
    main()
