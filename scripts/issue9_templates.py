"""Reproduce the offline template anchors for issue #9; no inference calls.

Install transformers==4.56.2 and jinja2==3.1.6 in a local environment.
Run from the repository root: python scripts/issue9_templates.py
Only public tokenizer/template files are downloaded, at pinned revisions.
"""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

from jinja2 import TemplateError
from transformers import AutoTokenizer


ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / "docs/issue9-template-inputs.json").read_text())
    probes = json.loads((ROOT / "stealthprint/data/probes.json").read_text())
    base = probes["base"]
    fixtures = {
        "base": [{"role": "user", "content": base}],
        "base_zh": [{"role": "user", "content": base + dict(probes["probes"])["zh_long"]}],
        "base_trimmed": [{"role": "user", "content": base.strip()}],
        "explicit_system": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": base},
        ],
        "two_turns": [
            {"role": "user", "content": base},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "Repeat that."},
        ],
    }
    results = {
        "kind": "offline_template_counts_not_hosted_model_identification",
        "versions": {p: importlib.metadata.version(p) for p in ("transformers", "tokenizers", "jinja2")},
        "fixed_template_date": config["fixed_template_date"],
        "fixtures": fixtures,
        "unavailable_official_tokenizers": config["unavailable"],
        "models": [],
        "reasoning_inference": {"status": "not_run", "reason": "No configured credential for the Pareto endpoint."},
    }
    for model in config["models"]:
        print("Measuring", model["repo"], flush=True)
        directory = ROOT / "tok/issue9" / model["repo"].replace("/", "--") / model["revision"]
        directory.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name in model["files"]:
            if name not in {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "chat_template.jinja"}:
                continue
            path = directory / name
            if not path.exists():
                url = f'{config["download_base"]}/{model["repo"]}/resolve/{model["revision"]}/{name}'
                temporary = path.with_suffix(path.suffix + ".download")
                subprocess.run(["curl", "-fsSL", "--connect-timeout", "15", "--max-time", "180", url, "-o", str(temporary)], check=True)
                temporary.replace(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        variants = {"default": {}}
        if model["repo"].startswith("deepcogito/"):
            variants["enable_thinking"] = {"enable_thinking": True}
        if model["repo"].startswith("NousResearch/Hermes-4"):
            variants["thinking"] = {"thinking": True}
            variants["thinking_keep_cots"] = {"thinking": True, "keep_cots": True}
        measured = {"repo": model["repo"], "revision": model["revision"], "sha256": hashes, "variants": {}}
        for variant, kwargs in variants.items():
            rows = {}
            for fixture, messages in fixtures.items():
                raw = sum(len(tokenizer.encode(m["content"], add_special_tokens=False)) for m in messages)
                common = dict(add_generation_prompt=True, date_string=config["fixed_template_date"], **kwargs)
                try:
                    rendered = tokenizer.apply_chat_template(messages, tokenize=False, **common)
                    ids = tokenizer.apply_chat_template(messages, tokenize=True, **common)
                except TemplateError as error:
                    rows[fixture] = {"status": "template_error", "type": type(error).__name__, "message": str(error)}
                    continue
                encoded = tokenizer.encode(rendered, add_special_tokens=False)
                if ids != encoded:
                    raise RuntimeError(f"Template/tokenization mismatch: {model['repo']} {variant} {fixture}")
                without_header = tokenizer.apply_chat_template(messages, tokenize=True, **{**common, "add_generation_prompt": False})
                rows[fixture] = {
                    "status": "ok",
                    "raw_content_tokens": raw,
                    "template_tokens": len(ids),
                    "overhead": len(ids) - raw,
                    "generation_prompt_tokens": len(ids) - len(without_header),
                    "rendered": rendered,
                }
            measured["variants"][variant] = rows
        results["models"].append(measured)
        print({v: {k: row.get("overhead", row["status"]) for k, row in rows.items()} for v, rows in measured["variants"].items()}, flush=True)
    output = ROOT / "docs/issue9-template-results.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print("Saved", output, flush=True)


if __name__ == "__main__":
    main()
