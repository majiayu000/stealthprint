"""Catalog watch regressions with a synthetic catalog; no network."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

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


if __name__ == "__main__":
    unittest.main()
