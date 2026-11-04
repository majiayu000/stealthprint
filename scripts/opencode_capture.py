#!/usr/bin/env python3
"""Capture one real opencode CLI request as a reusable envelope (credentials removed).

OpenCode Zen's free tier (/zen/v1) only serves requests that look like the real
client: its system prompt, its tool list, its session headers. This script runs a
local logging proxy, points opencode at it through OPENCODE_CONFIG, forwards the
request to opencode.ai, and saves the first chat request with the Authorization
header stripped. scripts/opencode_relay.mjs then replays that envelope.

The envelope is sent again with every replayed request, so local identity in the
captured system prompt (rule files such as ~/.claude/CLAUDE.md, working directory, home
path, user name, host name, temp paths, skill list) is removed unless --raw is given. Inspect the result before use: this
removes the patterns below, not everything a prompt could contain.

usage: python3 -I scripts/opencode_capture.py --model opencode/<free-model> --out envelope.json
       python3 -I scripts/opencode_capture.py --scrub old.json --out envelope.json
"""
import argparse
import http.server
import json
import getpass
import os
import re
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

UPSTREAM = "https://opencode.ai"
DROP = {"host", "content-length", "accept-encoding", "connection"}


def scrub_text(text, home, user, host):
    """Replace local identity in one string."""
    # opencode appends the user's own rule files verbatim; they run to the end of the prompt.
    text = re.sub(r"\n*Instructions from: .*\Z", "", text, flags=re.S)
    text = re.sub(r"(Working directory|Workspace root folder): [^\n]*", r"\1: /tmp/workspace", text)
    text = re.sub(r"<available_skills>.*?</available_skills>", "<available_skills></available_skills>", text, flags=re.S)
    text = re.sub(r"(?:/private)?/(?:var/folders|tmp)/[^\s\"'<>]+", "/tmp/workspace", text)
    if home:
        text = text.replace(home, "/home/user")
    for name, placeholder in ((user, "user"), (host, "host")):
        if name and len(name) >= 3:
            text = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(name), placeholder, text)
    return text


def scrub(value, home, user, host):
    """Apply scrub_text to every string inside a JSON value."""
    if isinstance(value, str):
        return scrub_text(value, home, user, host)
    if isinstance(value, list):
        return [scrub(v, home, user, host) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, home, user, host) for k, v in value.items()}
    return value


def scrub_envelope(envelope):
    return scrub(envelope, os.path.expanduser("~"), getpass.getuser(), socket.gethostname().split(".")[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="opencode model id, e.g. opencode/pulse-alpha-free")
    ap.add_argument("--out", required=True, help="where to write the envelope JSON")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--raw", action="store_true", help="keep local paths and names in the envelope")
    ap.add_argument("--scrub", help="scrub an existing envelope instead of capturing one")
    args = ap.parse_args()
    if args.scrub:
        with open(args.scrub, encoding="utf-8") as fh:
            cleaned = scrub_envelope(json.load(fh))
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(cleaned, fh, ensure_ascii=False, indent=1)
        print("scrubbed envelope written to %s" % args.out)
        return
    if not args.model:
        ap.error("--model is required unless --scrub is given")
    saved = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            if (not saved.is_set() and self.path.endswith("/chat/completions") and parsed
                    and parsed.get("model") == args.model.split("/", 1)[-1]):
                env = {"path": self.path,
                       "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
                       "body": parsed}
                if not args.raw:
                    env = scrub_envelope(env)
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(env, fh, ensure_ascii=False, indent=1)
                saved.set()
            req = urllib.request.Request(UPSTREAM + self.path, data=body, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=300)
            except urllib.error.HTTPError as e:
                resp = e
            data = resp.read()
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "opencode.json")
        with open(cfg, "w") as fh:
            json.dump({"provider": {"opencode": {"options": {
                "baseURL": "http://127.0.0.1:%d/zen/v1" % args.port}}}}, fh)
        env = dict(os.environ, OPENCODE_CONFIG=cfg)
        # The forwarded copy may be refused (the free tier rejects non-bun
        # clients); only the outgoing request shape matters here.
        subprocess.run(["opencode", "run", "-m", args.model, "Reply with just: ok"],
                       env=env, cwd=d, timeout=300, capture_output=True,
                       stdin=subprocess.DEVNULL)  # opencode run waits on an open stdin
    server.shutdown()
    if not saved.is_set():
        raise SystemExit("no matching chat request captured (is the model id right?)")
    print("envelope written to %s (Authorization removed%s)" % (args.out, "" if args.raw else ", local identity scrubbed"))


if __name__ == "__main__":
    main()
