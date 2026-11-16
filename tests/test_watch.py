"""Catalog watch regressions with a synthetic catalog; no network."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from stealthprint import watch
from stealthprint.cli import main


def catalog(*ids):
    return lambda url, api_key=None: [{"id": i, "name": i.upper(), "created": 1760000000} for i in ids]


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def sightings(self):
        path = os.path.join(self.state, "openrouter", "sightings.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]

    def test_first_pass_is_a_silent_baseline(self):
        r = watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        self.assertTrue(r["baseline"])
        self.assertEqual(r["events"], [])
        self.assertEqual(self.sightings(), [])

    def test_added_and_removed_ids_become_sightings(self):
        watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        r = watch.run_watch(self.state, fetch=catalog("a/x", "stealth/ox-alpha"), now=now, verbose=False)
        self.assertEqual((r["added"], r["removed"]), (["stealth/ox-alpha"], ["b/y"]))
        events = self.sightings()
        self.assertEqual([e["event"] for e in events], ["added", "removed"])
        self.assertTrue(events[0]["stealth_hint"])
        self.assertFalse(events[1]["stealth_hint"])
        self.assertEqual(events[0]["observed_at"], now.isoformat())
        self.assertTrue(events[0]["catalog_created"].startswith("2025-10-09"))

    def test_unchanged_catalog_appends_nothing(self):
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        self.assertEqual(self.sightings(), [])

    def test_evidence_is_attached_only_to_added_ids(self):
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        seen = []

        def on_added(ids):
            seen.append(ids)
            return {"n/new": {"l1_best": "glm5"}}

        watch.run_watch(self.state, fetch=catalog("n/new"), on_added=on_added, verbose=False)
        self.assertEqual(seen, [["n/new"]])
        added = [e for e in self.sightings() if e["event"] == "added"]
        self.assertEqual(added[0]["evidence"], {"l1_best": "glm5"})

    def test_events_are_queued_only_for_a_webhook(self):
        pending = os.path.join(self.state, "openrouter", watch.WEBHOOK_PENDING)
        sent = []

        def deliver():
            return watch.deliver_webhook(self.state, "openrouter", "hook", post=lambda url, events: sent.append(events))

        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        self.assertFalse(os.path.exists(pending))
        self.assertEqual((deliver(), sent), (0, []))
        watch.run_watch(self.state, fetch=catalog("a/x"), queue_webhook=True, verbose=False)
        with open(pending, "a", encoding="utf-8") as stream:
            stream.write('{"event": "add')        # a write cut short must not block the queue
        self.assertEqual(deliver(), 1)
        self.assertEqual([(e["event"], e["model"]) for e in sent[0]], [("removed", "b/y")])
        self.assertFalse(os.path.exists(pending))

    def test_empty_catalog_keeps_previous_snapshot(self):
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        with self.assertRaises(RuntimeError):
            watch.run_watch(self.state, fetch=catalog(), verbose=False)
        r = watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        self.assertEqual(r["removed"], [])

    def test_malformed_entries_are_ignored(self):
        index = watch.index_catalog([{"id": ""}, {"id": 3}, "x", {"id": "ok", "created": True}])
        self.assertEqual(list(index), ["ok"])
        self.assertIsNone(index["ok"]["created"])

    def test_sources(self):
        self.assertEqual(watch.resolve_source("openrouter")[1], watch.SOURCES["openrouter"])
        name, url = watch.resolve_source("https://api.example.com/v1/models")
        self.assertEqual((name, url), ("api-example-com-v1-models", "https://api.example.com/v1/models"))
        with self.assertRaises(ValueError):
            watch.resolve_source("nope")


class LoopbackTests(unittest.TestCase):
    """Real urllib calls against servers on 127.0.0.1."""

    def serve(self, respond):
        """respond(method, path, headers, body) -> (status, headers, body); every request is logged."""
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                seen.append({"method": self.command, "path": self.path,
                             "authorization": self.headers.get("Authorization"), "body": body})
                status, headers, payload = respond(self.command, self.path, self.headers, body)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = handle_request

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_port, seen

    def redirecting(self):
        """A server answering /<status> with that redirect, and what its target received."""
        target, reached = self.serve(lambda *request: (200, {}, b'{"data": [{"id": "a/x"}]}'))
        source, _ = self.serve(lambda method, path, *rest: (
            int(path.rsplit("/", 1)[1]), {"Location": target + "/elsewhere"}, b""))
        return source, reached

    def test_catalog_redirect_cannot_forward_the_key(self):
        source, reached = self.redirecting()
        with self.assertRaisesRegex(RuntimeError, "302.*/elsewhere"):
            watch.fetch_catalog(source + "/v1/models/302", api_key="dummy-only")
        self.assertEqual(reached, [])

    def test_webhook_redirect_is_an_error_not_a_delivery(self):
        # urllib follows 301/302/303 by turning the POST into a GET without the events.
        source, reached = self.redirecting()
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, "%d.*/elsewhere" % status):
                    watch.post_webhook("%s/hook/%d" % (source, status), [{"event": "added", "model": "n/new"}])
                self.assertEqual(reached, [])

    def test_webhook_posts_events_as_json(self):
        url, seen = self.serve(lambda *request: (204, {}, b""))
        self.assertEqual(watch.post_webhook(url + "/hook", [{"event": "added", "model": "n/new"}]), 204)
        self.assertEqual([(r["method"], r["path"], json.loads(r["body"])) for r in seen],
                         [("POST", "/hook", {"events": [{"event": "added", "model": "n/new"}]})])

    def test_events_a_webhook_did_not_accept_are_sent_on_the_next_run(self):
        ids, hook_status = ["a/x"], [503]
        catalog_url, _ = self.serve(lambda *request: (
            200, {}, json.dumps({"data": [{"id": i} for i in ids]}).encode()))
        hook_url, hooked = self.serve(lambda *request: (hook_status[0], {}, b""))

        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                return main(["watch", "--source", catalog_url + "/v1/models", "--state", state,
                             "--webhook", hook_url + "/hook"])

        def delivered():
            return [[e["model"] for e in json.loads(r["body"])["events"]] for r in hooked]

        with tempfile.TemporaryDirectory() as state:
            self.assertEqual(run(), 0)                      # baseline
            ids.append("stealth/new-alpha")
            with self.assertRaises(SystemExit) as failed:   # the webhook is down for this pass
                run()
            self.assertIn("503", str(failed.exception.code))
            self.assertEqual(delivered(), [["stealth/new-alpha"]])
            hook_status[0] = 200
            ids.append("b/later")
            self.assertEqual(run(), 0)
            self.assertEqual(delivered()[1:], [["stealth/new-alpha", "b/later"]])
            self.assertEqual(run(), 0)                      # nothing is sent twice
            self.assertEqual(len(hooked), 2)


if __name__ == "__main__":
    unittest.main()
