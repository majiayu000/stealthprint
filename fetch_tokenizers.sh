#!/usr/bin/env bash
# Freeze each successful candidate revision in tok/sources.lock.
set -euo pipefail
exec python3 - "${1:-tok}" "$(dirname "$0")/tokenizers.lock" <<'PY'
import hashlib
import http.client
import json
from pathlib import Path
import sys
import urllib.request

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
manifest = out / 'sources.lock'
# The committed lock pins every revision, so a fresh checkout gets the same files.
committed = Path(sys.argv[2]) if len(sys.argv) > 2 else None
sources = json.loads(committed.read_text()) if committed and committed.exists() else {}
if manifest.exists():
    sources.update(json.loads(manifest.read_text()))
candidates = [
    ('zai-org/GLM-5', 'glm5'),
    ('zai-org/GLM-5.3-Flash', 'glm53'),
    ('zai-org/GLM-4.6', 'glm46'),
    ('Qwen/Qwen3-0.6B', 'qwen3'),
    ('Qwen/Qwen3-Next-80B-A3B-Instruct', 'qwen3next'),
    ('deepseek-ai/DeepSeek-V3', 'deepseek'),
    ('dots-studio/dots3-note-prev', 'dots3'),
    ('rednote-hilab/dots.llm1.inst', 'dotsllm1'),
    ('MiniMaxAI/MiniMax-M1', 'minimax'),
    ('MiniMaxAI/MiniMax-M2', 'minimax-m2'),
    ('NousResearch/Meta-Llama-3.1-8B-Instruct', 'llama3'),
    # meta-llama repos are gated; this mirror carries the same tokenizer.json.
    ('unsloth/Llama-4-Scout-17B-16E-Instruct', 'llama4'),
    ('mistralai/Mistral-Nemo-Instruct-2407', 'mistral-nemo-tekken'),
    ('mistralai/Mistral-Small-3.1-24B-Instruct-2503', 'mistral-small31'),
    ('XiaomiMiMo/MiMo-V2.5', 'mimo-v2.5'),
    ('moonshotai/Kimi-K2-Instruct', 'kimi-k2'),
    ('tencent/Hunyuan-7B-Instruct', 'hunyuan7b'),
    ('tencent/Hunyuan-A13B-Instruct', 'hunyuan-a13b'),
    ('tencent/Hunyuan-MT-7B', 'hunyuan-mt'),
    ('tencent/Hunyuan-1.8B-Instruct', 'hunyuan18'),
    ('stepfun-ai/step3', 'step3'),
    ('baidu/ERNIE-4.5-0.3B-PT', 'ernie45'),
    ('unsloth/gemma-3-1b-it', 'gemma3'),
    ('unsloth/gemma-3n-E2B-it', 'gemma3n'),
    ('aisingapore/Gemma-SEA-LION-v4-27B-IT', 'sealion'),
    ('scb10x/typhoon2.1-gemma3-4b', 'typhoon'),
    ('inclusionAI/Ling-mini-2.0', 'ling2'),
    ('inclusionAI/Ring-1T', 'ring1t'),
    ('ByteDance-Seed/Seed-OSS-36B-Instruct', 'seedoss'),
    ('microsoft/phi-4', 'phi4'),
    ('swiss-ai/Apertus-8B-Instruct-2509', 'apertus'),
    ('openbmb/MiniCPM4-8B', 'minicpm4'),
    ('meituan-longcat/LongCat-Flash-Chat', 'longcat'),
    ('ibm-granite/granite-4.0-h-small', 'granite4'),
    ('tiiuae/Falcon-H1-7B-Instruct', 'falconh1'),
    ('arcee-ai/Trinity-Mini', 'trinity'),
    ('ServiceNow-AI/Apriel-1.5-15b-Thinker', 'apriel'),
    ('LGAI-EXAONE/EXAONE-4.0-32B', 'exaone4'),
    ('allenai/Olmo-3-7B-Instruct', 'olmo3'),
    ('baichuan-inc/Baichuan-M2-32B', 'baichuan'),
    ('nvidia/NVIDIA-Nemotron-Nano-12B-v2', 'nemotron-nano2'),
    ('skt/A.X-4.0-Light', 'ax4'),
    ('naver-hyperclovax/HyperCLOVAX-SEED-Think-14B', 'hyperclova-think'),
    ('kakaocorp/kanana-1.5-8b-instruct-2505', 'kanana'),
    ('krutrim-ai-labs/Krutrim-2-instruct', 'krutrim'),
    ('upstage/SOLAR-10.7B-Instruct-v1.0', 'solar1'),
    ('tokyotech-llm/Llama-3.3-Swallow-70B-Instruct-v0.4', 'swallow'),
    ('trillionlabs/Tri-7B', 'tri'),
]
# Vocabularies published as tiktoken files rather than tokenizer.json.
TIKTOKEN_FILES = {'kimi-k2': 'tiktoken.model'}
failed = []
for repo, name in candidates:
    remote = TIKTOKEN_FILES.get(name, 'tokenizer.json')
    destination = out / (name + ('.tiktoken' if name in TIKTOKEN_FILES else '.json'))
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
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{remote}'
        print(f'fetching {repo}@{revision}', flush=True)
        with urllib.request.urlopen(url, timeout=180) as response:
            raw = response.read()
        if name not in TIKTOKEN_FILES:
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
if committed:
    committed.write_text(json.dumps(sources, indent=2, sort_keys=True) + '\n')
print('Available candidates:', ', '.join(sorted(p.stem for p in [*out.glob('*.json'), *out.glob('*.tiktoken')])))
print('o200k_base and cl100k_base are loaded by tiktoken.')
if failed:
    print('Unavailable downloads:', ', '.join(failed), file=sys.stderr)
    print('Gated or non-JSON tokenizers need their published format; they were not substituted.', file=sys.stderr)
    sys.exit(1)
PY
