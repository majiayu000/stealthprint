import argparse
import json
import os
import sys

from .client import ChatClient
from .i18n import set_lang, get_lang, VALID_LANGS, load_probes, t
from . import layers


def make_client(args):
    try:
        return ChatClient(model=args.model, base_url=args.base_url, api_key=args.api_key)
    except ValueError as e:
        sys.exit(str(e))


def emit(result, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    # CLI help language follows env at parse time; output messages follow --lang.
    ap = argparse.ArgumentParser(prog="stealthprint", description=t("cli.desc"))
    ap.add_argument("--base-url", default=os.environ.get("STEALTHPRINT_BASE_URL"))
    ap.add_argument("--model", default=os.environ.get("STEALTHPRINT_MODEL"))
    ap.add_argument("--api-key", default=os.environ.get("STEALTHPRINT_API_KEY"))
    ap.add_argument("--lang", default=get_lang(), choices=VALID_LANGS)
    ap.add_argument("--json", action="store_true", help="also print machine-readable JSON")
    # same flags accepted after the subcommand too (hidden, same dests)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=os.environ.get("STEALTHPRINT_BASE_URL"), help=argparse.SUPPRESS)
    common.add_argument("--model", default=os.environ.get("STEALTHPRINT_MODEL"), help=argparse.SUPPRESS)
    common.add_argument("--api-key", default=os.environ.get("STEALTHPRINT_API_KEY"), help=argparse.SUPPRESS)
    common.add_argument("--lang", default=get_lang(), choices=VALID_LANGS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tokenizer", parents=[common], help=t("cli.tok"))
    p.add_argument("--probes", default=None, help="probe set json (default: bundled multilingual set)")
    p.add_argument("--tokenizers", default="tok", help="dir of local tokenizer.json candidates")
    sub.add_parser("wrapper", parents=[common], help=t("cli.wrap"))
    p = sub.add_parser("context", parents=[common], help=t("cli.ctx"))
    p.add_argument("--max-bytes", type=int, default=4_500_000)
    p.add_argument("--needle-size", type=int, default=500_000, help="approx prompt_tokens for needle haystack")
    p.add_argument("--skip-search", action="store_true", help="skip binary search, run needle test only")
    sub.add_parser("errors", parents=[common], help=t("cli.err"))
    p = sub.add_parser("vision", parents=[common], help=t("cli.vision"))
    p.add_argument("--repeats", type=int, default=0,
                   help="if >0, run N identical 64x64 color probes instead of the size/color grid")
    sub.add_parser("video", parents=[common], help=t("cli.video"))
    p = sub.add_parser("catalog", parents=[common], help=t("cli.cat"))
    p.add_argument("--peers", default=None, help="comma-separated model ids to A/B on this gateway")
    p.add_argument("--family", default=None, help="substring filter on GET /v1/models ids (e.g. glm)")

    args = ap.parse_args(argv)
    set_lang(args.lang)
    if not args.model:
        sys.exit(t("err.no_model"))
    client = make_client(args)

    if args.cmd == "tokenizer":
        probes = load_probes(args.probes) if args.probes else load_probes()
        r = layers.tokenizer_differential(client, probes=probes, tokenizers_dir=args.tokenizers)
    elif args.cmd == "wrapper":
        r = layers.wrapper_constant(client)
    elif args.cmd == "context":
        r = {} if args.skip_search else layers.context_search(client, max_bytes=args.max_bytes)
        r["needle"] = layers.needle_test(client, prompt_tokens_size=args.needle_size)
    elif args.cmd == "errors":
        r = layers.error_family(client)
    elif args.cmd == "vision":
        r = layers.vision_repeat(client, n=args.repeats) if args.repeats else layers.vision_truth(client)
    elif args.cmd == "video":
        r = layers.video_probe(client)
    elif args.cmd == "catalog":
        peers = [x.strip() for x in args.peers.split(",")] if args.peers else None
        r = layers.catalog_ab(client, peers=peers, family=args.family)
    emit(r, args.json)


if __name__ == "__main__":
    main()
