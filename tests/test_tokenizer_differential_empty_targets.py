"""Regression: tokenizer_differential must not ZeroDivisionError when all probes fail."""

import math
import unittest

from stealthprint.layers import tokenizer_differential


class StubClient:
    """Minimal ChatClient stand-in: prompt_tokens(messages) -> (tokens|None, err|None)."""

    def __init__(self, base_tokens=10, probe_results=None):
        self.base_tokens = base_tokens
        # probe_results: list of (tokens|None, err|None) consumed in probe order
        self.probe_results = list(probe_results or [])
        self._calls = 0

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        self._calls += 1
        if self._calls == 1:
            return self.base_tokens, None
        if not self.probe_results:
            return None, {"http": 500, "body": "probe failed"}
        return self.probe_results.pop(0)


PROBES = {
    "base": "BASE ",
    "probes": [
        ["fr", "bonjour"],
        ["ru", "привет"],
        ["thai", "สวัสดี"],
    ],
}


class TestTokenizerDifferentialEmptyTargets(unittest.TestCase):
    def test_all_probes_fail_raises_runtime_error(self):
        client = StubClient(
            base_tokens=10,
            probe_results=[
                (None, {"http": 500, "body": "fail1"}),
                (None, {"http": 500, "body": "fail2"}),
                (None, {"http": 500, "body": "fail3"}),
            ],
        )
        with self.assertRaises(RuntimeError) as ctx:
            tokenizer_differential(
                client, probes=PROBES, tokenizers_dir="/nonexistent-tok-dir", verbose=False
            )
        msg = str(ctx.exception).lower()
        self.assertNotIn("division", msg)
        self.assertIn("probe", msg)

    def test_one_successful_probe_yields_finite_ranking(self):
        # base=10; one probe returns 25 => delta 15; other probes fail
        client = StubClient(
            base_tokens=10,
            probe_results=[
                (25, None),
                (None, {"http": 500, "body": "fail"}),
                (None, {"http": 500, "body": "fail"}),
            ],
        )
        result = tokenizer_differential(
            client, probes=PROBES, tokenizers_dir="/nonexistent-tok-dir", verbose=False
        )
        self.assertEqual(result["api_delta"], {"fr": 15})
        self.assertTrue(result["ranking"])
        for name, entry in result["ranking"].items():
            self.assertGreater(entry["total"], 0, name)
            self.assertTrue(math.isfinite(entry["mae"]), name)


if __name__ == "__main__":
    unittest.main()
