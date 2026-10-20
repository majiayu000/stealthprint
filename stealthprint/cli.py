import argparse
import contextlib
import json
import os
import sys

from .client import ChatClient
from .i18n import set_lang, get_lang, VALID_LANGS, load_probes, t
from . import layers, probes_extra, survey, watch


def make_client(args):
    try:
        return ChatClient(model=args.model, base_url=args.base_url, api_key=args.api_key,
                          transport=args.transport, record_path=args.record,
                          shell=getattr(args, "shell", None),
                          session_id=getattr(args, "session_id", None))
    except (ValueError, OSError) as e:
        sys.exit(str(e))


def emit(result, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def build_parser():
    # CLI help language follows env at parse time; output messages follow --lang.
    ap = argparse.ArgumentParser(prog="stealthprint", description=t("cli.desc"))
    ap.add_argument("--base-url", default=os.environ.get("STEALTHPRINT_BASE_URL"))
    ap.add_argument("--model", default=os.environ.get("STEALTHPRINT_MODEL"))
    ap.add_argument("--api-key", default=os.environ.get("STEALTHPRINT_API_KEY"))
    ap.add_argument("--lang", default=get_lang(), choices=VALID_LANGS)
    ap.add_argument("--json", action="store_true", help="JSON only on stdout; progress on stderr")
    ap.add_argument("--transport", choices=("openai", "anthropic"), default="openai", help="Anthropic: text counting only")
    ap.add_argument("--record", help="append raw requests/responses to JSONL, without authentication headers")
    ap.add_argument("--shell", default=None, choices=["opencode-free"],
                    help="client-identity shell for gateways with fingerprint checks")
    ap.add_argument("--session-id", default=os.environ.get("STEALTHPRINT_SESSION_ID"),
                    help="client session id (required by opencode-free shell: reuse one "
                         "created by a real opencode CLI run)")
    # same flags accepted after the subcommand too (hidden, same dests).
    # SUPPRESS defaults keep the subparser from clobbering values the main
    # parser already stored when the flag only appears before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--model", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--api-key", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--lang", default=argparse.SUPPRESS, choices=VALID_LANGS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--transport", choices=("openai", "anthropic"), default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--record", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--shell", default=argparse.SUPPRESS, choices=["opencode-free"], help=argparse.SUPPRESS)
    common.add_argument("--session-id", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tokenizer", parents=[common], help=t("cli.tok"))
    p.add_argument("--probes", default=None, help="probe set json (default: bundled multilingual set)")
    p.add_argument("--tokenizers", default="tok", help="dir of local tokenizer.json candidates")
    p.add_argument("--repeats", type=int, default=0,
                   help="if >0, interleave N samples per text; unstable counts are retained without ranking")
    p = sub.add_parser("wrapper", parents=[common], help=t("cli.wrap"))
    p.add_argument("--turns", type=int, default=0,
                   help="if >0, run the multi-turn accumulation probe instead of the length ladder")
    p = sub.add_parser("echo", parents=[common], help=t("cli.echo"))
    p.add_argument("--probes", default=None, help="probe set json (default: bundled multilingual set)")
    p.add_argument("--tokenizers", default="tok", help="dir of local tokenizer.json candidates")
    sub.add_parser("tools", parents=[common], help=t("cli.tools"))
    p = sub.add_parser("context", parents=[common], help=t("cli.ctx"))
    p.add_argument("--max-chars", type=int, default=4_500_000)
    p.add_argument("--needle-size", type=int, default=500_000, help="approx prompt_tokens for needle haystack")
    p.add_argument("--skip-search", action="store_true", help="skip input-length ladder, run needle test only")
    sub.add_parser("errors", parents=[common], help=t("cli.err"))
    p = sub.add_parser("vision", parents=[common], help=t("cli.vision"))
    p.add_argument("--repeats", type=int, default=0,
                   help="if >0, run N identical 64x64 color probes instead of the size/color grid")
    sub.add_parser("video", parents=[common], help=t("cli.video"))
    p = sub.add_parser("catalog", parents=[common], help=t("cli.cat"))
    p.add_argument("--peers", default=None, help="comma-separated model ids to A/B on this gateway")
    p.add_argument("--family", default=None, help="substring filter on GET /v1/models ids (e.g. glm)")
    p = sub.add_parser("survey", parents=[common], help=t("cli.survey"))
    p.add_argument("--tokenizers", default="tok", help="dir of local tokenizer.json candidates")
    p.add_argument("--filter", default=None, help="substring filter on catalog ids")
    p.add_argument("--max-models", type=int, default=0,
                   help="stop after N models; does not cap requests or cost (0 = no limit)")
    p.add_argument("--out", default=None,
                   help="append one JSON line per model here; only completed models with matching experiment config are skipped")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between models (free tiers allow ~1 concurrent)")
    p.add_argument("--retries", type=int, default=1, help="retries per probe on failure")
    p = sub.add_parser("watch", parents=[common], help=t("cli.watch"))
    p.add_argument("--source", default="openrouter",
                   help="catalog to poll: openrouter or an OpenAI-compatible /models URL")
    p.add_argument("--state", default="watch-state", help="directory for snapshots and sightings.jsonl")
    p.add_argument("--fingerprint", action="store_true",
                   help="L1-probe added ids via --base-url/--api-key (billable requests)")
    p.add_argument("--tokenizers", default="tok", help="dir of local tokenizer.json candidates")
    p.add_argument("--webhook", default=None, help="POST new events as JSON to this URL (off by default)")
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    language = argparse.ArgumentParser(add_help=False)
    language.add_argument("--lang", choices=VALID_LANGS, default=get_lang())
    set_lang(language.parse_known_args(argv)[0].lang)
    args = build_parser().parse_args(argv)
    set_lang(args.lang)
    if args.cmd == "watch":
        try:
            with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
                r = run_watch(args)
        except (ValueError, RuntimeError, OSError) as error:
            sys.exit(str(error))
        emit(r, args.json)
        return 0
    if not args.model and args.cmd != "survey":
        sys.exit(t("err.no_model"))
    client = make_client(args)

    if args.transport == "anthropic" and args.cmd not in ("tokenizer", "wrapper"):
        sys.exit("Anthropic CLI supports tokenizer/wrapper text counts only")
    try:
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            r = run(args, client)
    except (ValueError, RuntimeError, OSError) as error:
        sys.exit(str(error))
    emit(r, args.json)
    return 1 if r.get("error") or r.get("status") in ("incomplete", "unstable_counts") else 0


def run_watch(args):
    on_added = None
    if args.fingerprint:
        if not args.base_url or not args.api_key:
            sys.exit("watch --fingerprint needs --base-url and --api-key for the probed gateway")

        def on_added(ids):
            def factory(m):
                return ChatClient(model=m or ids[0], base_url=args.base_url, api_key=args.api_key,
                                  transport=args.transport, record_path=args.record)
            measured = survey.run_survey(factory, ids, tokenizers_dir=args.tokenizers)["measured"]
            return {m: {"l1_best": row.get("best"), "l1_scores": row.get("scores"),
                        "mode": row.get("mode")} for m, row in measured.items()}

    catalog_key = args.api_key if args.source != "openrouter" else None
    r = watch.run_watch(args.state, source=args.source, api_key=catalog_key, on_added=on_added)
    if args.webhook and r["events"]:
        watch.post_webhook(args.webhook, r["events"])
    return r


def run(args, client):
    if args.cmd == "tokenizer":
        probes = load_probes(args.probes) if args.probes else load_probes()
        if args.repeats > 0:
            r = probes_extra.tokenizer_repeats(client, probes=probes,
                                               tokenizers_dir=args.tokenizers,
                                               repeats=args.repeats)
        else:
            r = layers.tokenizer_differential(client, probes=probes, tokenizers_dir=args.tokenizers)
    elif args.cmd == "wrapper":
        if args.turns > 0:
            r = probes_extra.wrapper_turns(client, turns=args.turns)
        else:
            r = layers.wrapper_constant(client)
    elif args.cmd == "echo":
        probes = load_probes(args.probes) if args.probes else load_probes()
        r = probes_extra.echo_verify(client, probes=probes, tokenizers_dir=args.tokenizers)
    elif args.cmd == "tools":
        r = probes_extra.tools_probe(client)
    elif args.cmd == "context":
        r = {} if args.skip_search else layers.context_search(client, max_chars=args.max_chars)
        if not r.get("error"):
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
    elif args.cmd == "survey":
        from .client import require_openai
        require_openai(client)
        ids, err = client.list_models()
        if err or not ids:
            sys.exit("catalog fetch failed: %s" % (err or "empty catalog"))

        def factory(m):
            if m is None or m == args.model:
                return client
            return ChatClient(model=m, base_url=args.base_url, api_key=args.api_key,
                              transport=args.transport, record_path=args.record)

        r = survey.run_survey(factory, ids, tokenizers_dir=args.tokenizers,
                              filter_sub=args.filter, max_models=args.max_models,
                              out_path=args.out, delay=args.delay, retries=args.retries)
    return r


if __name__ == "__main__":
    sys.exit(main())
