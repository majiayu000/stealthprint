"""Regression: context_search must not report unverified max_verified_chars."""

import unittest

from stealthprint.layers import FILL, context_search

DEFAULT_TASK = "Answer with the single word OK and nothing else."


def _est_chars(content, task=DEFAULT_TASK):
    """Recover filler length used by ok_at (≈ requested chars, floored to FILL)."""
    prefix = task + "\n\n"
    if not content.startswith(prefix):
        return len(content)
    return len(content) - len(prefix)


class SizeGatedClient:
    """prompt_tokens succeeds iff filler length <= max_ok_filler (inclusive)."""

    def __init__(self, max_ok_filler, fail_nth_same=None):
        self.max_ok_filler = max_ok_filler
        self.fail_nth_same = fail_nth_same  # fail the Nth call at a previously-ok size
        self.calls = []
        self._by_filler = {}

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        content = messages[0]["content"]
        est = _est_chars(content)
        self.calls.append(est)
        n = self._by_filler.get(est, 0) + 1
        self._by_filler[est] = n
        if self.fail_nth_same is not None and n >= self.fail_nth_same and est <= self.max_ok_filler:
            return None, {"http": 500, "body": "flaky confirm"}
        if est <= self.max_ok_filler:
            return max(1, est // 4), None
        return None, {"http": 400, "body": "context length exceeded"}


class TestContextSearchVerifiedBound(unittest.TestCase):
    def test_a_all_sizes_fail_returns_null_not_10000(self):
        # Case A: every size fails → null, never unverified 10000 floor
        client = SizeGatedClient(max_ok_filler=-1)
        result = context_search(
            client, max_bytes=40_000, min_step=30_000, verbose=False
        )
        self.assertIsNone(result["max_verified_chars"])
        self.assertIsNone(result["max_verified_prompt_tokens"])
        self.assertIn("error", result)
        self.assertTrue(result["history"])
        self.assertTrue(all(not h["ok"] for h in result["history"]))
        self.assertNotEqual(result["max_verified_chars"], 10_000)

    def test_b_initial_10k_fails_no_downward_search(self):
        # Case B: 10k floor fails; policy is null/error (no downward search)
        floor_filler = (10_000 // len(FILL)) * len(FILL)
        client = SizeGatedClient(max_ok_filler=floor_filler - 1)
        result = context_search(
            client, max_bytes=200_000, min_step=30_000, verbose=False
        )
        self.assertIsNone(result["max_verified_chars"])
        self.assertIsNone(result["max_verified_prompt_tokens"])
        self.assertIn("error", result)
        # Only the initial bound probe — no downward search for smaller sizes
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["chars"], 10_000)
        self.assertFalse(result["history"][0]["ok"])

    def test_c_boundary_search_gated_by_final_ok_at(self):
        # Case C: 10k OK, mids succeed then fail → last verified lo with tokens
        floor_filler = (10_000 // len(FILL)) * len(FILL)
        # Allow up to ~80k filler so binary search finds a ceiling below max_bytes
        max_ok = 80_000
        self.assertGreater(max_ok, floor_filler)
        client = SizeGatedClient(max_ok_filler=max_ok)
        result = context_search(
            client, max_bytes=200_000, min_step=30_000, verbose=False
        )
        self.assertIsNotNone(result["max_verified_chars"])
        self.assertLessEqual(
            (result["max_verified_chars"] // len(FILL)) * len(FILL), max_ok
        )
        self.assertIsNotNone(result["max_verified_prompt_tokens"])
        self.assertTrue(any(h["ok"] for h in result["history"]))
        self.assertTrue(any(not h["ok"] for h in result["history"]))
        # Returned size must appear as a successful probe in history (verified)
        verified_chars = {h["chars"] for h in result["history"] if h["ok"]}
        self.assertIn(result["max_verified_chars"], verified_chars)
        matching = next(
            h for h in result["history"] if h["chars"] == result["max_verified_chars"] and h["ok"]
        )
        self.assertEqual(
            result["max_verified_prompt_tokens"], matching["prompt_tokens"]
        )

    def test_c_final_ok_at_failure_falls_back_to_history(self):
        # Final confirmation fails: still return last history success, not unverified lo
        floor_filler = (10_000 // len(FILL)) * len(FILL)
        client = SizeGatedClient(max_ok_filler=floor_filler, fail_nth_same=2)
        result = context_search(
            client, max_bytes=40_000, min_step=30_000, verbose=False
        )
        # hi-lo <= min_step so no mid loop; initial OK, final confirm fails
        self.assertEqual(result["max_verified_chars"], 10_000)
        self.assertIsNotNone(result["max_verified_prompt_tokens"])
        self.assertTrue(result["history"][0]["ok"])

    def test_d_happy_path_when_ceiling_found(self):
        # Case D: high ceiling — search converges with verified bound
        client = SizeGatedClient(max_ok_filler=500_000)
        result = context_search(
            client, max_bytes=200_000, min_step=30_000, verbose=False
        )
        self.assertIsNotNone(result["max_verified_chars"])
        self.assertGreaterEqual(result["max_verified_chars"], 10_000)
        self.assertIsNotNone(result["max_verified_prompt_tokens"])
        self.assertNotIn("error", result)
        verified_chars = {h["chars"] for h in result["history"] if h["ok"]}
        self.assertIn(result["max_verified_chars"], verified_chars)


if __name__ == "__main__":
    unittest.main()
