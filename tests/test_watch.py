"""Catalog watch regressions with a synthetic catalog; no network."""
import contextlib
import errno
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

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

    def test_a_pass_that_overlaps_another_does_nothing(self):
        # cron starts the next pass while --fingerprint is still probing the last one's new ids
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        probed, refused = [], []

        def overlapping(call):
            try:
                call()
            except RuntimeError as error:
                refused.append(str(error))

        def on_added(ids):
            probed.append(ids)
            overlapping(lambda: watch.run_watch(self.state, fetch=catalog("a/x", "n/new"),
                                                on_added=probed.append, verbose=False))
            overlapping(lambda: watch.deliver_webhook(self.state, "openrouter", "hook", post=probed.append))

        watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), on_added=on_added,
                        queue_webhook=True, verbose=False)
        self.assertEqual(probed, [["n/new"]])
        self.assertEqual([e["model"] for e in self.sightings()], ["n/new"])
        self.assertEqual([("still running" in message) for message in refused], [True, True])
        # the lock goes with the pass, so the next one runs
        self.assertEqual(watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)["removed"], ["n/new"])

    def interrupted_pass(self, *ids):
        """A pass that appends its sightings and dies before latest.json moves."""
        probed = []
        with mock.patch.object(watch.os, "replace", side_effect=OSError("killed here")):
            with self.assertRaisesRegex(OSError, "killed here"):
                watch.run_watch(self.state, fetch=catalog(*ids), on_added=probed.append, verbose=False)
        return probed

    def test_a_pass_that_died_before_latest_moved_is_not_recorded_twice(self):
        watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        probed = self.interrupted_pass("a/x", "n/new")
        recorded = [("added", "n/new"), ("removed", "b/y")]
        self.assertEqual([(e["event"], e["model"]) for e in self.sightings()], recorded)
        r = watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), on_added=probed.append, verbose=False)
        self.assertEqual(r["events"], [])
        self.assertEqual(probed, [["n/new"]])               # not probed, and billed, a second time
        self.assertEqual([(e["event"], e["model"]) for e in self.sightings()], recorded)

    def test_the_pass_after_an_interrupted_one_records_only_what_changed_since(self):
        watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        self.interrupted_pass("a/x", "n/new")
        # n/new is gone again and b/y is still gone: only the first is news
        r = watch.run_watch(self.state, fetch=catalog("a/x", "n/later"), verbose=False)
        self.assertEqual((r["added"], r["removed"]), (["n/later"], ["n/new"]))
        self.assertEqual([(e["event"], e["model"]) for e in self.sightings()],
                         [("added", "n/new"), ("removed", "b/y"), ("added", "n/later"), ("removed", "n/new")])

    def test_a_pass_that_died_between_its_two_logs_still_reaches_the_webhook(self):
        # an event that is in sightings.jsonl is not found again, so the queue must not be the later write
        watch.run_watch(self.state, fetch=catalog("a/x"), verbose=False)
        appends = []

        def second_append_fails(path, mode="r", **kwargs):
            if "a" in mode and path.endswith(".jsonl"):
                appends.append(path)
                if len(appends) == 2:
                    raise OSError("killed here")
            return open(path, mode, **kwargs)

        with mock.patch.object(watch, "open", second_append_fails, create=True):
            with self.assertRaisesRegex(OSError, "killed here"):
                watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), queue_webhook=True, verbose=False)
        watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), queue_webhook=True, verbose=False)
        sent = []
        watch.deliver_webhook(self.state, "openrouter", "hook", post=lambda url, events: sent.extend(events))
        self.assertIn("n/new", [e["model"] for e in sent])
        self.assertEqual([e["model"] for e in self.sightings()], ["n/new"])

    def pass_cut_short(self, state, fetch, cut_inside, **options):
        """A pass whose first write to a log stops one byte into `cut_inside`, as on a full disk."""
        class CutShort:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def write(self, text):
                data = text.encode("utf-8")
                with open(self.path, "ab") as stream:
                    stream.write(data[:data.index(cut_inside) + 1])
                raise OSError(errno.ENOSPC, "No space left on device")

        def appending(path, mode="r", **kwargs):
            if "a" in mode and path.endswith(".jsonl"):
                return CutShort(path)
            return open(path, mode, **kwargs)

        with mock.patch.object(watch, "open", appending, create=True):
            with self.assertRaisesRegex(OSError, "No space left"):
                watch.run_watch(state, fetch=fetch, verbose=False, **options)

    def test_events_queued_behind_a_write_cut_short_are_still_delivered(self):
        # latest.json did not move, so the next pass finds both ids again and queues them behind the half line
        def named(*ids):
            return lambda url, api_key=None: [{"id": i, "name": "模型 " + i} for i in ids]

        for cut_inside in (b'"model"', "模".encode("utf-8")):   # the second stops inside a character
            with self.subTest(cut_inside=cut_inside), tempfile.TemporaryDirectory() as state:
                pending = os.path.join(state, "openrouter", watch.WEBHOOK_PENDING)
                watch.run_watch(state, fetch=named("a/x"), verbose=False)
                self.pass_cut_short(state, named("a/x", "n/new", "n/other"), cut_inside, queue_webhook=True)
                with open(pending, "rb") as stream:
                    self.assertFalse(stream.read().endswith(b"\n"))
                watch.run_watch(state, fetch=named("a/x", "n/new", "n/other"), queue_webhook=True, verbose=False)
                sent = []
                watch.deliver_webhook(state, "openrouter", "hook", post=lambda url, events: sent.extend(events))
                self.assertEqual([e["model"] for e in sent], ["n/new", "n/other"])
                self.assertFalse(os.path.exists(pending))

    def test_a_sighting_recorded_behind_a_write_cut_short_keeps_its_own_line(self):
        watch.run_watch(self.state, fetch=catalog("a/x", "b/y"), verbose=False)
        self.pass_cut_short(self.state, catalog("a/x", "n/new"), b'"removed"')   # n/new is whole, b/y is half a line
        r = watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), verbose=False)
        self.assertEqual((r["added"], r["removed"]), ([], ["b/y"]))
        whole = []
        with open(os.path.join(self.state, "openrouter", "sightings.jsonl"), "rb") as stream:
            for line in stream:
                with contextlib.suppress(ValueError):
                    whole.append(json.loads(line))
        self.assertEqual([(e["event"], e["model"]) for e in whole], [("added", "n/new"), ("removed", "b/y")])

    def test_sightings_from_before_the_snapshot_are_not_replayed(self):
        # latest.json written before it recorded how much of sightings.jsonl it covers
        directory = os.path.join(self.state, "openrouter")
        os.makedirs(directory)
        with open(os.path.join(directory, "latest.json"), "w", encoding="utf-8") as stream:
            json.dump({"source": "openrouter", "models": {"a/x": {}}}, stream)
        with open(os.path.join(directory, "sightings.jsonl"), "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "removed", "model": "a/x"}) + "\n")   # a/x came back later
        r = watch.run_watch(self.state, fetch=catalog("a/x", "n/new"), verbose=False)
        self.assertEqual((r["added"], r["removed"]), (["n/new"], []))

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
