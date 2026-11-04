import json
import os
import tempfile
import unittest
from unittest import mock

from stealthprint import fingerprint as fp
from stealthprint.client import ChatClient, parse_sse_chunks


def sse(*events):
    return "\n\n".join("data: " + json.dumps(e) for e in events) + "\n\ndata: [DONE]\n"


class SSEChunkTests(unittest.TestCase):
    def test_keeps_each_delta_and_usage(self):
        raw = sse({"choices": [{"delta": {"reasoning_content": "think"}}]},
                  {"choices": [{"delta": {"content": "The"}}]},
                  {"choices": [{"delta": {"content": " quick"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}],
                   "usage": {"completion_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 1}}})
        out = parse_sse_chunks(raw)
        self.assertEqual(out["chunks"], ["The", " quick"])
        self.assertEqual(out["reasoning_chunks"], 1)
        self.assertEqual(out["finish_reason"], "stop")
        self.assertEqual(out["usage"]["completion_tokens"], 7)

    def test_ignores_garbage_lines(self):
        out = parse_sse_chunks(": keep-alive\ndata: not json\ndata: [1,2]\n" + sse({"choices": [{"delta": {"content": "x"}}]}))
        self.assertEqual(out["chunks"], ["x"])


class BoundaryTests(unittest.TestCase):
    def test_identical_segmentation_scores_one(self):
        a = ["The", " quick", " brown"]
        self.assertEqual(fp.boundary_scores(a, a), (3, 0, 0))
        self.assertEqual(fp.f1(3, 0, 0), 1.0)

    def test_split_word_counts_false_positive(self):
        tp, fpos, fn = fp.boundary_scores([" fox"], [" ", "fox"])
        self.assertEqual((tp, fpos, fn), (1, 1, 0))
        self.assertLess(fp.f1(tp, fpos, fn), 1.0)

    def test_strip_whitespace_edges(self):
        self.assertEqual(fp.strip_ws_edges(["\n", "a", " b", " "]), ["a", " b"])

    def test_key_pattern_marks_observed_split_and_unknown(self):
        samples = [{"pieces": ["The", " quick", " ", "fox"]}, {"pieces": ["学习", "机", "器"]}]
        pattern = fp.key_pattern_from_pieces(samples)
        keys = dict(zip(fp.KEY_TOKENS, pattern))
        self.assertEqual(keys["学习"], "1")
        self.assertEqual(keys["机器"], ".")
        self.assertEqual(keys[" fox"], ".")
        self.assertEqual(keys["안녕하세요"], "?")


class ErrorClassifierTests(unittest.TestCase):
    def test_sglang_pd_serde_stack(self):
        text = ('Upstream request failed: [prefill_bad_request] Prefill server error (400 Bad Request): '
                '{"message":"max_completion_tokens is too large: 99999999.This model supports at most '
                '1048576 completion tokens."} [airlock_error] invalid type: string "abc", expected i64 at line 1 column 9')
        out = fp.classify_error(text)
        for tag in ("sglang", "pd_disaggregated", "rust_serde", "airlock_gateway"):
            self.assertIn(tag, out["tags"])
        self.assertEqual(out["limits"]["max_completion"], 1048576)

    def test_vendor_signatures(self):
        cases = {
            "gemini_api": "Invalid value at 'generation_config.seed' (TYPE_INT32)",
            "anthropic_api": "max_tokens: 99999999 > 128000, which is the maximum allowed number of output tokens",
            "minimax_api": "model[MiniMax-M3.1-Flash] does not support max tokens > 524288 (2013)",
            "dashscope_api": "Range of max_tokens should be [1, 131072]",
        }
        for tag, text in cases.items():
            with self.subTest(tag=tag):
                self.assertIn(tag, fp.classify_error(text)["tags"])
        self.assertEqual(fp.classify_error(cases["anthropic_api"])["limits"]["max_completion"], 128000)

    def test_context_limit(self):
        out = fp.classify_error("The input (1048576 tokens) is longer than the model's context length (1048576 tokens).")
        self.assertEqual(out["limits"]["context"], 1048576)


class VisionFitTests(unittest.TestCase):
    PULSE = [(32, 32, 4), (64, 64, 7), (256, 256, 52), (512, 512, 172),
             (1024, 1024, 679), (2048, 2048, 2707), (1024, 256, 185)]

    def test_recovers_40px_plus_3(self):
        fit = fp.fit_grid_formula(self.PULSE)
        self.assertEqual((fit["kind"], fit["patch"], fit["const"], fit["exact"]), ("grid", 40, 3, 7))

    def test_fixed_cost(self):
        fit = fp.fit_grid_formula([(32, 32, 1091), (1024, 1024, 1091)])
        self.assertEqual(fit["kind"], "fixed")

    def test_errors_ignored(self):
        fit = fp.fit_grid_formula([(32, 32, None), (64, 64, "x")])
        self.assertEqual(fit["kind"], "none")


class ProfileMatchTests(unittest.TestCase):
    def test_match_requires_all_hard_layers(self):
        a = {"model": "a", "l1_delta": {"x": 1, "y": 2}, "vision": {"tokens": {"64x64": 7}}}
        same = {"model": "b", "l1_delta": {"x": 1, "y": 2}, "vision": {"tokens": {"64x64": 7}}}
        other = {"model": "c", "l1_delta": {"x": 1, "y": 3}, "vision": {"tokens": {"64x64": 9}}}
        rows = fp.match_profile(a, {"a": a, "b": same, "c": other})
        self.assertEqual([r["model"] for r in rows], ["b", "c"])
        self.assertEqual(rows[0]["verdict"], "match")
        self.assertEqual(rows[1]["verdict"], "different")

    def test_library_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.json"), "w") as fh:
                json.dump({"model": "m", "l1_delta": {"x": 1}}, fh)
            self.assertIn("m", fp.load_library(d))


class FakeCounter:
    """prompt_tokens stub: overhead plus a per-text count."""

    def __init__(self, counter, overhead=100, fail=()):
        self.counter, self.overhead, self.fail = counter, overhead, fail
        self.calls = []

    def prompt_tokens(self, messages, max_tokens=1, extra=None):
        text = messages[0]["content"]
        self.calls.append((text, max_tokens))
        if text in self.fail:
            return None, {"http": 500, "body": "boom"}
        return self.overhead + self.counter(text), None


class CountIntegrityTests(unittest.TestCase):
    def test_tokenizer_like_counts_are_trusted(self):
        client = FakeCounter(lambda text: max(1, len(text) // 4))
        out = fp.count_integrity(client, verbose=False)
        self.assertEqual(out["status"], "trusted", out["reasons"])
        self.assertEqual(out["letter_spread"], 0)
        self.assertTrue(all(abs(p["residual"]) <= fp.MAX_ADDITIVITY_RESIDUAL for p in out["pairs"]))

    def test_requests_clear_a_sixteen_token_output_minimum(self):
        client = FakeCounter(lambda text: 1)
        fp.count_integrity(client, verbose=False)
        self.assertEqual({mt for _, mt in client.calls}, {16})

    def test_recorded_crouton_counts_are_untrusted(self):
        # Values measured on crouton-alpha-free, 2026-11-04.
        a, b = fp.INTEGRITY_PAIRS[0]
        seen = {"a": 19939, "b": 19937, "c": 19936, "d": 19937, "e": 19937, "f": 19936, "g": 19936, "h": 19935,
                "x": 19935, a: 19949, b: 19949, a + b: 19958}
        client = FakeCounter(lambda text: seen.get(text, 19935 + len(text) // 4), overhead=0)
        out = fp.count_integrity(client, verbose=False)
        self.assertEqual(out["status"], "untrusted")
        self.assertEqual(out["letter_spread"], 4)
        self.assertEqual(out["pairs"][0]["residual"], -6)
        self.assertEqual(len(out["reasons"]), 2)

    def test_additivity_alone_fails(self):
        a, b = fp.INTEGRITY_PAIRS[1]
        client = FakeCounter(lambda text: 30 if text == a + b else (1 if len(text) == 1 else 10))
        out = fp.count_integrity(client, verbose=False)
        self.assertEqual(out["status"], "untrusted")
        self.assertEqual(out["letter_spread"], 0)

    def test_changing_count_for_same_request_fails(self):
        seen_a = []

        def counter(text):
            if text == "a":
                seen_a.append(1)
                return 1 + len(seen_a)
            return 1 if len(text) == 1 else 10
        out = fp.count_integrity(FakeCounter(counter), verbose=False)
        self.assertFalse(out["repeat_stable"])
        self.assertEqual(out["status"], "untrusted")

    def test_failed_requests_are_incomplete_not_trusted(self):
        client = FakeCounter(lambda text: 1 if len(text) == 1 else 10, fail=("x",))
        out = fp.count_integrity(client, verbose=False)
        self.assertEqual(out["status"], "incomplete")
        self.assertIn("x", out["failures"])
        self.assertTrue(all("residual" not in p for p in out["pairs"]))


class EvidenceExclusionTests(unittest.TestCase):
    L1 = {"x": 1, "y": 2}

    def test_untrusted_target_drops_count_layers(self):
        target = {"model": "t", "count_integrity": {"status": "untrusted"}, "l1_delta": self.L1,
                  "vision": {"tokens": {"64x64": 7}}}
        peer = {"model": "p", "l1_delta": {"x": 9, "y": 9}, "vision": {"tokens": {"64x64": 9}}}
        row = fp.match_profile(target, {"p": peer})[0]
        self.assertEqual(row["verdict"], "no_overlap")
        self.assertTrue(row["count_untrusted"])
        self.assertNotIn("l1", row)
        self.assertNotIn("vision", row)

    def test_untrusted_library_entry_drops_count_layers(self):
        target = {"model": "t", "l1_delta": self.L1}
        peer = {"model": "p", "l1_delta": self.L1, "count_integrity": {"status": "untrusted"}}
        self.assertEqual(fp.match_profile(target, {"p": peer})[0]["verdict"], "no_overlap")

    def test_trusted_and_unchecked_profiles_still_compare(self):
        target = {"model": "t", "count_integrity": {"status": "trusted"}, "l1_delta": self.L1}
        peer = {"model": "p", "l1_delta": self.L1}
        self.assertEqual(fp.match_profile(target, {"p": peer})[0]["verdict"], "match")

    def test_all_zero_vision_is_not_agreement(self):
        zero = {"tokens": {"64x64": 0, "256x256": 0}}
        a = {"model": "a", "l1_delta": {"x": 1, "y": 2}, "vision": zero}
        b = {"model": "b", "l1_delta": {"x": 5, "y": 2}, "vision": zero}
        row = fp.match_profile(a, {"b": b})[0]
        self.assertEqual(row["verdict"], "different")
        self.assertTrue(row["vision_uncounted"])
        self.assertNotIn("vision", row)

    def test_batched_stream_is_not_segmentation_evidence(self):
        batched = {"per_token_chunks": False, "samples": [{"name": "s", "pieces": ["ab cd"]}]}
        per_token = {"per_token_chunks": True, "samples": [{"name": "s", "pieces": ["ab", " cd"]}]}
        row = fp.match_profile({"model": "a", "segmentation": batched},
                               {"b": {"model": "b", "segmentation": per_token}})[0]
        self.assertEqual(row["verdict"], "no_overlap")
        self.assertTrue(row["segmentation_unusable"])
        self.assertNotIn("segmentation_f1", row)

    def test_build_profile_records_integrity(self):
        prof = fp.build_profile("m", integrity={"status": "untrusted"})
        self.assertTrue(fp.counts_untrusted(prof))


class SegmentationLayerTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatClient(model="t", base_url="https://example.invalid/v1", api_key="dummy")

    def test_per_token_check_and_no_tokenizers(self):
        def fake(messages, max_tokens=0, extra=None):
            return {"chunks": ["The", " quick"], "usage": {"completion_tokens": 6,
                    "completion_tokens_details": {"reasoning_tokens": 2}}}, None
        with mock.patch.object(self.client, "stream_chat", side_effect=fake):
            out = fp.segmentation(self.client, tokenizers_dir=None,
                                  texts=[("a", "The quick"), ("b", "The quick")], verbose=False)
        self.assertTrue(out["per_token_chunks"])
        self.assertEqual(out["offsets"], [2])
        self.assertEqual(out["usable"], 2)

    def test_inconsistent_offsets_warn(self):
        usages = iter([4, 9])

        def fake(messages, max_tokens=0, extra=None):
            return {"chunks": ["The quick"], "usage": {"completion_tokens": next(usages)}}, None
        with mock.patch.object(self.client, "stream_chat", side_effect=fake):
            out = fp.segmentation(self.client, tokenizers_dir=None,
                                  texts=[("a", "The quick"), ("b", "The quick")], verbose=False)
        self.assertFalse(out["per_token_chunks"])
        self.assertIn("warning", out)


if __name__ == "__main__":
    unittest.main()
