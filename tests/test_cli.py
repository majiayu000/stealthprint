import unittest

from stealthprint.cli import build_parser


class FlagPlacementTests(unittest.TestCase):
    """Global flags must work before OR after the subcommand (README promise)."""

    def test_flags_before_subcommand(self):
        ns = build_parser().parse_args(["--model", "m", "--base-url", "https://x/v1", "wrapper"])
        self.assertEqual(ns.model, "m")
        self.assertEqual(ns.base_url, "https://x/v1")
        self.assertEqual(ns.cmd, "wrapper")

    def test_flags_after_subcommand(self):
        ns = build_parser().parse_args(["wrapper", "--model", "m"])
        self.assertEqual(ns.model, "m")

    def test_json_flag_before_subcommand_survives(self):
        ns = build_parser().parse_args(["--json", "wrapper"])
        self.assertIs(ns.json, True)

    def test_json_flag_after_subcommand(self):
        ns = build_parser().parse_args(["wrapper", "--json"])
        self.assertIs(ns.json, True)

    def test_subcommand_flag_overrides_global(self):
        ns = build_parser().parse_args(["--model", "a", "wrapper", "--model", "b"])
        self.assertEqual(ns.model, "b")

    def test_defaults_when_no_flags(self):
        ns = build_parser().parse_args(["wrapper"])
        self.assertIsNone(ns.model)
        self.assertIs(ns.json, False)


if __name__ == "__main__":
    unittest.main()
