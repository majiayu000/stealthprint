#!/usr/bin/env bash
# Download open tokenizer.json files used as local comparison candidates.
# Usage: ./fetch_tokenizers.sh [outdir]   (default: tok)
set -euo pipefail
OUT="${1:-tok}"
mkdir -p "$OUT"
while IFS=: read -r repo name; do
  [ -z "$repo" ] && continue
  echo "fetching $repo -> $OUT/$name.json"
  curl -fsSL -m 180 "https://huggingface.co/$repo/resolve/main/tokenizer.json" -o "$OUT/$name.json" \
    || { echo "  FAILED $repo (gated or missing; skipped)"; rm -f "$OUT/$name.json"; }
done <<'EOF'
zai-org/GLM-5:glm5
zai-org/GLM-5.3-Flash:glm53
Qwen/Qwen3-0.6B:qwen3
deepseek-ai/DeepSeek-V3:deepseek
dots-studio/dots3-note-prev:dots3
MiniMaxAI/MiniMax-M1:minimax
NousResearch/Meta-Llama-3.1-8B-Instruct:llama3
EOF
# o200k comes from tiktoken (pip install tiktoken), no file needed
echo "done. note: moonshotai/* does not publish tokenizer.json; add your own candidates to the list."
