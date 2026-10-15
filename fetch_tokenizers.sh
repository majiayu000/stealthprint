#!/usr/bin/env bash
# Freeze each successful candidate revision in tok/sources.lock.
set -euo pipefail
exec python3 - "${1:-tok}" <<'PY'
import hashlib
import http.client
import json
from pathlib import Path
import sys
import urllib.request

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
manifest = out / 'sources.lock'
sources = json.loads(manifest.read_text()) if manifest.exists() else {}
candidates = [
    ('zai-org/GLM-5', 'glm5'),
    ('zai-org/GLM-5.3-Flash', 'glm53'),
    ('Qwen/Qwen3-0.6B', 'qwen3'),
    ('deepseek-ai/DeepSeek-V3', 'deepseek'),
    ('dots-studio/dots3-note-prev', 'dots3'),
    ('MiniMaxAI/MiniMax-M1', 'minimax'),
    ('NousResearch/Meta-Llama-3.1-8B-Instruct', 'llama3'),
    ('mistralai/Mistral-Nemo-Instruct-2407', 'mistral-nemo-tekken'),
    ('XiaomiMiMo/MiMo-V2.5', 'mimo-v2.5'),
    ('meta-llama/Llama-4-Scout-17B-16E-Instruct', 'llama4'),
    ('moonshotai/Kimi-K2-Instruct', 'kimi-k2'),
]
failed = []
for repo, name in candidates:
    destination = out / (name + '.json')
    temporary = out / (name + '.download')
    try:
        source = sources.get(name)
        if source and source['repo'] != repo:
            raise ValueError('source changed; use a separate candidate directory')
        if source:
            revision = source['revision']
        else:
            with urllib.request.urlopen('https://huggingface.co/api/models/' + repo, timeout=30) as response:
                revision = json.load(response)['sha']
        url = f'https://huggingface.co/{repo}/resolve/{revision}/tokenizer.json'
        print(f'fetching {repo}@{revision}', flush=True)
        with urllib.request.urlopen(url, timeout=180) as response:
            raw = response.read()
        json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if source and source['sha256'] != digest:
            raise ValueError('pinned candidate hash mismatch')
        temporary.write_bytes(raw)
        temporary.replace(destination)
        sources[name] = {'repo': repo, 'revision': revision, 'sha256': digest}
        pending = out / 'sources.lock.tmp'
        pending.write_text(json.dumps(sources, indent=2) + '\n')
        pending.replace(manifest)
    except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
        temporary.unlink(missing_ok=True)
        failed.append(name)
        print(f'FAILED {name}: {error}; existing candidate retained', file=sys.stderr)
print('Available JSON candidates:', ', '.join(p.stem for p in sorted(out.glob('*.json'))))
print('o200k_base and cl100k_base are loaded by tiktoken.')
if failed:
    print('Unavailable downloads:', ', '.join(failed), file=sys.stderr)
    print('Gated or non-JSON tokenizers need their published format; they were not substituted.', file=sys.stderr)
    sys.exit(1)
PY
