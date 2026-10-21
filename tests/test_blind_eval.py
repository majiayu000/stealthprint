"""Blind-evaluation scoring on synthetic survey rows; no network."""
import importlib.util
import os
import unittest

_spec = importlib.util.spec_from_file_location(
    "blind_eval", os.path.join(os.path.dirname(__file__), "..", "scripts", "blind_eval.py"))
blind_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blind_eval)

TRUTH = {"equivalent": [["qwen3", "mimo-v2.5"]],
         "models": {"q": "mimo-v2.5", "d": "deepseek", "w": "glm5", "c": None, "g": None, "m": "llama3"}}


def row(model, best, exact, n=10, complete=True):
    return {"model": model, "complete": complete, "best": best, "scores": {best: {"exact": exact, "n": n}}}


class BlindEvalTests(unittest.TestCase):
    def test_outcomes(self):
        rows = [row("q", "qwen3", 10), row("d", "deepseek", 10), row("w", "llama3", 9),
                row("c", "o200k_base", 3), row("g", "o200k_base", 10), row("m", "llama3", 5)]
        result = blind_eval.score_rows(rows, TRUTH)
        outcomes = {r["model"]: r["outcome"] for r in result["rows"]}
        self.assertEqual(outcomes, {"q": "correct", "d": "correct", "w": "wrong",
                                    "c": "correct_reject", "g": "false_positive", "m": "miss"})
        self.assertEqual(result["positive_accuracy"], 0.5)
        self.assertEqual(result["negative_reject_rate"], 0.5)
        self.assertEqual(result["precision_by_tier"]["high"], {"n": 3, "precision": 0.667})
        self.assertEqual(result["precision_by_tier"]["medium"], {"n": 1, "precision": 0.0})

    def test_vendor_attribution_separates_shared_vocabularies(self):
        truth = {"equivalent": [["qwen3", "mimo-v2.5"]],
                 "candidate_vendor": {"qwen3": "qwen", "mimo-v2.5": "xiaomi", "deepseek": "deepseek"},
                 "models": {"step": {"vocab": "deepseek", "vendor": "stepfun"},
                            "ds": {"vocab": "deepseek", "vendor": "deepseek"},
                            "mimo": {"vocab": "mimo-v2.5", "vendor": "xiaomi"}}}
        rows = [row("step", "deepseek", 10), row("ds", "deepseek", 10), row("mimo", "qwen3", 10)]
        result = blind_eval.score_rows(rows, truth)
        vendor = {r["model"]: (r["outcome"], r["vendor"]) for r in result["rows"]}
        self.assertEqual(vendor, {"step": ("correct", "wrong"), "ds": ("correct", "correct"),
                                  "mimo": ("correct", "ambiguous")})
        self.assertEqual(result["vendor_attribution"], {"correct": 1, "ambiguous": 1, "wrong": 1})

    def test_nonpositive_deltas_are_invalid_not_misses(self):
        bad = row("d", "deepseek", 3)
        bad["deltas"] = {"p1": 4, "p2": -2}
        result = blind_eval.score_rows([bad], TRUTH)
        self.assertEqual(result["invalid"], {"d": "nonpositive_delta"})
        self.assertEqual(result["graded"], 0)
        self.assertNotIn("d", result["missing"])

    def test_incomplete_and_unknown_rows_are_not_graded(self):
        result = blind_eval.score_rows([row("q", "qwen3", 10, complete=False), row("zzz", "qwen3", 10)], TRUTH)
        self.assertEqual(result["graded"], 0)
        self.assertIn("q", result["missing"])


if __name__ == "__main__":
    unittest.main()
