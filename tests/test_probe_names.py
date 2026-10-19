"""Synthetic probe-file validation regressions; no network or model dependencies."""
import json
import tempfile
import unittest
from pathlib import Path

from stealthprint.i18n import load_probes


class ProbeNameTests(unittest.TestCase):
    def load_names(self, names):
        data = {"base": "hi", "probes": [[name, " text"] for name in names]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probes.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_probes(path)

    def test_rejects_empty_name(self):
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.load_names([""])

    def test_rejects_non_string_names_before_uniqueness(self):
        for name in (None, True, False, 0, 1, 1.5, [], {}):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "strings"):
                    self.load_names([name])

    def test_rejects_names_that_collide_when_serialized(self):
        for names in ([None, "null"], [True, "true"], [1, "1"]):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    self.load_names(names)

    def test_preserves_existing_rejections(self):
        for names in ([], ["base"], ["same", "same"]):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    self.load_names(names)

    def test_preserves_distinct_nonempty_string_names(self):
        names = ["null", "true", "1", "探针", " ", "Base"]
        self.assertEqual(self.load_names(names)["probes"],
                         [[name, " text"] for name in names])

    def test_bundled_probes_remain_valid(self):
        data = load_probes()
        self.assertEqual(len(data["probes"]), 24)
        self.assertTrue(all(isinstance(name, str) and name
                            for name, _ in data["probes"]))


if __name__ == "__main__":
    unittest.main()
