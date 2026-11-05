#!/usr/bin/env python3
"""Recompute a published truncation result from its stored observations, without any request.

  python scripts/truncation_score.py docs/case-crouton-truncation.json tok
"""
import json
import sys

from stealthprint.layers import load_local_tokenizers
from stealthprint.truncation import rank_tokenizers


def main():
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    local = load_local_tokenizers(sys.argv[2])
    print("%d candidates" % len(local))
    for text_id, models in data["observations"].items():
        for model, rows in models.items():
            result = rank_tokenizers({text_id: rows}, local, texts=data["texts"])
            print("%s %-26s cuts=%-3d passing=%s" % (text_id, model, result["cuts"], result["passing"]))


if __name__ == "__main__":
    main()
