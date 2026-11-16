"""Catalog watch regressions with a synthetic catalog; no network."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from stealthprint import watch


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


if __name__ == "__main__":
    unittest.main()
