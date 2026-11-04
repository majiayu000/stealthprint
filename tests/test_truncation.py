"""Truncation boundaries, model-to-model comparison and the sentinel; no model API calls."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

from stealthprint import fingerprint as fp
from stealthprint import truncation as tr
from stealthprint.layers import visible_lengths

ROOT = Path(__file__).resolve().parents[1]
WORD = 4  # the fake model emits one token per WORD characters


class Encoding:
    def __init__(self, ids, offsets):
        self.ids, self.offsets = ids, offsets


class ByteSplitTokenizer:
    """Hugging Face shape: a 3-byte char is two tokens, offsets credit the char to both."""

    def encode(self, text, add_special_tokens=False):
        ids, offsets = [], []
        for i, ch in enumerate(text):
            if ord(ch) > 0x2000:
                ids += [(i, 0), (i, 1)]
                offsets += [(i, i + 1), (i, i + 1)]
            else:
                ids.append((i, 1))
                offsets.append((i, i + 1))
        self.text = text
        return Encoding(ids, offsets)

    def decode(self, ids, skip_special_tokens=False):
        shown = "".join(self.text[i] for i, last in ids if last)
        return shown + ("�" if ids and not ids[-1][1] else "")


class EchoClient:
    """Answers truncated echoes; `think` is how much of the budget hidden reasoning takes."""

    def __init__(self, think=lambda n: 0, reject=(), error=None):
        self.think, self.reject, self.error = think, reject, error
        self.calls = []

    def chat(self, messages, max_tokens=1, extra=None, **kwargs):
        effort = (extra or {}).get("reasoning_effort")
        self.calls.append((max_tokens, effort))
        if self.error:
            return None, {"http": 400, "body": self.error}
        if effort in self.reject:
            return None, {"http": 400, "body": "effort rejected"}
        text = messages[0]["content"].split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
        visible = max(0, max_tokens - self.think(max_tokens))
        content = text[:visible * WORD]
        finish = "stop" if len(content) >= len(text) else "length"
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {}}, None


class Fixed:
    """Boundaries every WORD chars, the tokenizer EchoClient actually uses."""

    def encode(self, text, add_special_tokens=False):
        ends = list(range(WORD, len(text), WORD)) + [len(text)]
        return Encoding(list(range(len(ends))), [(max(0, e - WORD), e) for e in ends])


class BoundaryTests(unittest.TestCase):
    def test_partial_character_token_shows_nothing_until_complete(self):
        text = "a机b"
        self.assertEqual(visible_lengths("x", ByteSplitTokenizer(), text), [1, 1, 2, 3])

    def test_local_pieces_merge_partial_characters(self):
        self.assertEqual(fp.local_pieces("x", ByteSplitTokenizer(), "a机b"), ["a", "机", "b"])

    def test_decoder_that_rewrites_text_falls_back_to_offsets(self):
        tk = ByteSplitTokenizer()
        with mock.patch.object(tk, "decode", return_value="zzz"):
            self.assertEqual(visible_lengths("x", tk, "ab"), [1, 2])

    def test_bundled_texts_match_published_data(self):
        data = json.loads((ROOT / "docs/case-crouton-truncation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["texts"], tr.TEXTS)
        self.assertEqual(data["prompt_template"], tr.PROMPT.replace("%s", "{text}"))


class LadderTests(unittest.TestCase):
    TEXT = "x" * 200

    def test_plain_ladder_collects_real_boundaries(self):
        rows, stats = tr.ladder(EchoClient(), self.TEXT, step=2, verbose=False)
        self.assertEqual(stats["stop"], "complete")
        self.assertTrue(all(cut == n * WORD for n, cut in rows))

    def test_empty_replies_widen_then_walk_back(self):
        client = EchoClient(think=lambda n: 30)
        rows, stats = tr.ladder(client, self.TEXT, step=2, verbose=False)
        budgets = [n for n, _ in client.calls]
        self.assertGreater(budgets[1] - budgets[0], 2)
        self.assertTrue(rows)
        # The first cut comes from the walk-back, close to where text first appears.
        self.assertLessEqual(min(n for n, _ in rows), 36)

    def test_varying_thinking_sweeps_again_instead_of_stopping_at_few_cuts(self):
        calls = []

        def think(n):
            calls.append(n)
            return 0 if len(calls) % 3 == 0 else n // 2  # some budgets leave far more text

        client = EchoClient(think=think)
        rows, stats = tr.ladder(client, self.TEXT, start=40, step=1, max_requests=40, verbose=False)
        budgets = [n for n, _ in client.calls]
        self.assertEqual(len(budgets), len(set(budgets)), "a budget was tried twice")
        self.assertNotEqual(stats["stop"], "errors")
        self.assertGreater(len(rows), 2)

    def test_fitting_text_with_enough_cuts_stops(self):
        client = EchoClient()
        _, stats = tr.ladder(client, "x" * 400, step=1, max_requests=200, verbose=False)
        self.assertEqual(stats["stop"], "complete")

    def test_repeated_errors_stop_early(self):
        client = EchoClient(error="This Go model trains on request data")
        rows, stats = tr.ladder(client, self.TEXT, verbose=False)
        self.assertEqual(rows, [])
        self.assertEqual(len(client.calls), tr.MAX_CONSECUTIVE_ERRORS)
        self.assertEqual(stats["stop"], "errors")
        self.assertIn("trains on request data", stats["last_error"])

    def test_request_cap_is_respected(self):
        client = EchoClient(think=lambda n: n)
        tr.ladder(client, self.TEXT, max_requests=5, verbose=False)
        self.assertLessEqual(len(client.calls), 5)

    def test_mistyped_echo_is_not_a_cut(self):
        client = mock.Mock()
        client.chat.return_value = ({"choices": [{"message": {"content": "wrong"}, "finish_reason": "length"}]}, None)
        row = tr.ask(client, "right text", 20)
        self.assertTrue(row["deviated"])
        self.assertNotIn("cut", row)

    def test_pick_effort_takes_lowest_accepted_with_text(self):
        client = EchoClient(reject=("none",))
        effort, tried = tr.pick_effort(client, self.TEXT, verbose=False)
        self.assertEqual(effort, "minimal")
        self.assertEqual(tried[0]["error"], "effort rejected")
        self.assertEqual(len(client.calls), 2)

    def test_pick_effort_keeps_an_accepted_value_when_thinking_eats_everything(self):
        effort, _ = tr.pick_effort(EchoClient(think=lambda n: n, reject=("none",)), self.TEXT, verbose=False)
        self.assertEqual(effort, "minimal")


class RankTests(unittest.TestCase):
    def test_no_cuts_passes_nothing(self):
        result = tr.rank_tokenizers({"t3": []}, {"a": Fixed()})
        self.assertEqual(result["passing"], [])
        self.assertEqual(result["evidence"], "weak")

    def test_true_tokenizer_passes_and_coarser_one_goes_over_budget(self):
        rows = [[n, n * WORD] for n in range(10, 50)]

        class Coarse(Fixed):
            def encode(self, text, add_special_tokens=False):
                ends = list(range(WORD // 2, len(text), WORD // 2)) + [len(text)]
                return Encoding(list(range(len(ends))), [(0, e) for e in ends])

        result = tr.rank_tokenizers({"t3": rows}, {"true": Fixed(), "fine": Coarse()})
        self.assertEqual(result["passing"], ["true"])
        self.assertEqual(result["ranking"]["fine"]["hit"], len(rows))
        self.assertGreater(result["ranking"]["fine"]["over"], 0)
        self.assertEqual(result["evidence"], "strong")

    def test_margin_shows_how_many_failures_separate_the_runner_up(self):
        rows = [[n, n * WORD] for n in range(10, 50)]

        class Miss(Fixed):
            def encode(self, text, add_special_tokens=False):
                ends = [e for e in range(WORD, len(text), WORD) if e != 40] + [len(text)]
                return Encoding(list(range(len(ends))), [(0, e) for e in ends])

        result = tr.rank_tokenizers({"t3": rows}, {"true": Fixed(), "near": Miss()})
        self.assertEqual(result["passing"], ["true"])
        self.assertEqual(result["ranking"]["near"]["hit"], len(rows) - 1)
        self.assertEqual(result["margin"], result["ranking"]["near"]["cuts"] - result["ranking"]["near"]["hit"] + result["ranking"]["near"]["over"])

    def test_probe_reports_error_without_cuts_and_loads_tokenizers_first(self):
        client = EchoClient(error="rate limited")
        client.transport = "openai"
        with mock.patch.object(tr, "load_local_tokenizers", return_value={"a": Fixed()}):
            out = tr.truncation_probe(client, text_ids=["t3"], effort="minimal", verbose=False)
        self.assertIn("rate limited", out["error"])
        self.assertEqual(len(client.calls), tr.MAX_CONSECUTIVE_ERRORS)
        with mock.patch.object(tr, "load_local_tokenizers", side_effect=RuntimeError("missing")), self.assertRaises(RuntimeError):
            tr.truncation_probe(client, verbose=False)
        self.assertEqual(len(client.calls), tr.MAX_CONSECUTIVE_ERRORS)


class StreamTests(unittest.TestCase):
    def client(self, chunks, completion, reasoning=0):
        client = mock.Mock()
        client.stream_chat.return_value = ({"chunks": chunks, "usage": {
            "completion_tokens": completion, "completion_tokens_details": {"reasoning_tokens": reasoning}}}, None)
        return client

    def test_per_token_stream_gives_boundaries(self):
        out = tr.stream_boundaries(self.client(["ab", "cd", "e"], 3), "abcde")
        self.assertEqual(out["boundaries"], [2, 4, 5])

    def test_batched_stream_gives_none(self):
        out = tr.stream_boundaries(self.client(["abcdefghijklmnopqrstuvwxyz"], 13), "abcdefghijklmnopqrstuvwxyz")
        self.assertFalse(out["per_token"])
        self.assertNotIn("boundaries", out)

    def test_estimated_usage_with_long_chunks_is_not_per_token(self):
        out = tr.stream_boundaries(self.client(["a" * 40, "b" * 40], 2), "a" * 40 + "b" * 40)
        self.assertFalse(out["per_token"])


def truncated(cuts=None, boundaries=None):
    text = {"observations": [[i + 16, c] for i, c in enumerate(cuts or [])]}
    if boundaries:
        text["stream"] = {"per_token": True, "boundaries": boundaries}
    return {"truncation": {"texts": {"t3": text}}}


class CompareTests(unittest.TestCase):
    def test_cuts_inside_other_models_boundaries_do_not_prove_a_match(self):
        cuts = list(range(2, 80, 2))
        cmp = fp.compare_profiles(truncated(cuts=cuts), truncated(boundaries=list(range(1, 100))))
        self.assertEqual(cmp["truncation_boundary"], {"checked": len(cuts), "conflicts": 0})
        self.assertEqual(fp.same_model_verdict(cmp), "no_overlap")

    def test_a_cut_off_the_other_models_boundaries_is_a_conflict(self):
        cmp = fp.compare_profiles(truncated(cuts=[3, 5]), truncated(boundaries=[2, 4, 5]))
        self.assertEqual(cmp["truncation_boundary"]["conflicts"], 1)
        self.assertEqual(fp.same_model_verdict(cmp), "different")

    def test_two_sets_of_cuts_cannot_contradict(self):
        self.assertNotIn("truncation_boundary", fp.compare_profiles(truncated(cuts=[1, 2]), truncated(cuts=[3, 4])))

    def test_shared_tokenizer_is_never_reported_as_the_same_model(self):
        cmp = fp.compare_profiles({"tokenizer_candidates": ["llama4"]}, {"tokenizer_candidates": ["llama4", "x"]})
        self.assertEqual(cmp["tokenizer_shared"], ["llama4"])
        self.assertEqual(fp.same_model_verdict(cmp), "tokenizer_match")

    def test_models_with_no_local_tokenizer_are_not_called_different(self):
        cmp = fp.compare_profiles({"tokenizer_candidates": []}, {"tokenizer_candidates": []})
        self.assertEqual(fp.same_model_verdict(cmp), "no_overlap")
        cmp = fp.compare_profiles({"tokenizer_candidates": []}, {"tokenizer_candidates": ["llama4"]})
        self.assertEqual(fp.same_model_verdict(cmp), "no_overlap")

    def test_disjoint_tokenizers_are_different(self):
        cmp = fp.compare_profiles({"tokenizer_candidates": ["a"]}, {"tokenizer_candidates": ["b"]})
        self.assertEqual(fp.same_model_verdict(cmp), "different")

    def test_candidates_ignore_l1_under_untrusted_counts_and_weak_truncation(self):
        l1 = {"ranking": {"llama4": {"exact": 24, "total": 24}, "x": {"exact": 3, "total": 24}}}
        self.assertEqual(fp.tokenizer_candidates({"status": "trusted"}, l1), ["llama4"])
        self.assertIsNone(fp.tokenizer_candidates({"status": "untrusted"}, l1))
        self.assertIsNone(fp.tokenizer_candidates(trunc={"evidence": "weak", "passing": ["a"]}))
        self.assertEqual(fp.tokenizer_candidates(trunc={"evidence": "strong", "passing": ["a"]}), ["a"])

    def test_profile_keeps_truncation_and_candidates(self):
        trunc = {"effort": "minimal", "cuts": 40, "evidence": "strong", "passing": ["llama4"], "texts": {"t3": {}}}
        profile = fp.build_profile("m", trunc=trunc)
        self.assertEqual(profile["tokenizer_candidates"], ["llama4"])
        self.assertEqual(profile["truncation"]["passing"], ["llama4"])


PROBES = {"base": "base ", "probes": [["p1", "one"], ["p2", "two"], ["p3", "three"]]}


class SentinelTests(unittest.TestCase):
    def run_sentinel(self, stored, status="trusted", deltas=None, trunc=None):
        client = mock.Mock()
        l1 = {"api_delta": deltas or {}, "ranking": {}}
        with mock.patch.object(fp, "count_integrity", return_value={"status": status}), \
             mock.patch.object(fp, "tokenizer_differential", return_value=l1) as measured, \
             mock.patch.object(fp.truncation_layer, "truncation_probe", return_value=trunc or {}):
            out = fp.sentinel(client, stored, PROBES, l1_probes=2, verbose=False)
        return out, measured

    def test_same_counts_are_unchanged_and_only_probe_a_subset(self):
        stored = {"model": "m", "count_integrity": {"status": "trusted"}, "l1_delta": {"p1": 1, "p2": 2, "p3": 3}}
        out, measured = self.run_sentinel(stored, deltas={"p1": 1, "p2": 2})
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(len(measured.call_args.kwargs["probes"]["probes"]), 2)

    def test_different_l1_is_changed(self):
        stored = {"model": "m", "count_integrity": {"status": "trusted"}, "l1_delta": {"p1": 1, "p2": 2}}
        out, _ = self.run_sentinel(stored, deltas={"p1": 1, "p2": 9})
        self.assertEqual(out["status"], "changed")
        self.assertEqual(out["l1_different"], {"p2": [2, 9]})

    def test_counts_turning_untrusted_is_changed(self):
        stored = {"model": "m", "count_integrity": {"status": "trusted"}, "l1_delta": {"p1": 1}}
        out, measured = self.run_sentinel(stored, status="untrusted")
        self.assertEqual(out["status"], "changed")
        self.assertIn("trusted -> untrusted", out["changes"][0])
        measured.assert_not_called()

    def test_untrusted_profile_is_checked_through_truncation(self):
        stored = {"model": "m", "count_integrity": {"status": "untrusted"}, "tokenizer_candidates": ["llama4"]}
        same, _ = self.run_sentinel(stored, status="untrusted", trunc={"cuts": 20, "passing": ["llama4"]})
        self.assertEqual(same["status"], "unchanged")
        moved, _ = self.run_sentinel(stored, status="untrusted", trunc={"cuts": 20, "passing": ["o200k_base"]})
        self.assertEqual(moved["status"], "changed")

    def test_nothing_to_compare_is_unverifiable_not_unchanged(self):
        out, _ = self.run_sentinel({"model": "m", "count_integrity": {"status": "untrusted"}}, status="untrusted")
        self.assertEqual(out["status"], "unverifiable")


class ProfileCliTests(unittest.TestCase):
    def test_partial_profile_survives_a_failing_layer(self):
        import contextlib
        import io
        import tempfile
        from stealthprint.cli import main
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p.json"
            with mock.patch.object(fp, "count_integrity", return_value={"status": "untrusted"}), \
                 mock.patch("stealthprint.layers.tokenizer_differential", side_effect=RuntimeError("cut off")), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaises(SystemExit):
                main(["--base-url", "http://127.0.0.1:1", "--model", "m", "--api-key", "k",
                      "profile", "--out", str(out)])
            self.assertEqual(json.loads(out.read_text())["count_integrity"]["status"], "untrusted")

    def test_untrusted_counts_add_truncation(self):
        import contextlib
        import io
        import tempfile
        from stealthprint.cli import main
        trunc = {"effort": "minimal", "cuts": 40, "evidence": "strong", "passing": ["llama4"], "texts": {}}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p.json"
            with mock.patch.object(fp, "count_integrity", return_value={"status": "untrusted"}), \
                 mock.patch.object(tr, "truncation_probe", return_value=trunc) as probe, \
                 contextlib.redirect_stdout(io.StringIO()):
                main(["--base-url", "http://127.0.0.1:1", "--model", "m", "--api-key", "k", "profile",
                      "--out", str(out), "--skip", "l1,segment,leaks,vision"])
            probe.assert_called_once()
            self.assertEqual(json.loads(out.read_text())["tokenizer_candidates"], ["llama4"])


class ScrubTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("opencode_capture", ROOT / "scripts/opencode_capture.py")
        self.capture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.capture)

    def scrub(self, value):
        return self.capture.scrub(value, "/Users/alice", "alice", "alice-mbp")

    def test_identity_is_removed_everywhere_in_the_envelope(self):
        envelope = {"headers": {"x": "/Users/alice/work"},
                    "body": {"messages": [{"content": "cwd /Users/alice/code on alice-mbp by alice in /private/var/folders/ab/T/tmp1/x"}]}}
        text = json.dumps(self.scrub(envelope))
        self.assertNotIn("alice", text)
        self.assertNotIn("/var/folders", text)
        self.assertIn("/home/user/code", text)

    def test_user_rule_files_are_removed(self):
        prompt = "# Tool policy\n<env>\n  Working directory: /Users/alice/p\n</env>\nInstructions from: /Users/alice/.claude/CLAUDE.md\n# Rules\nprivate"
        out = self.scrub(prompt)
        self.assertNotIn("private", out)
        self.assertNotIn("Instructions from", out)
        self.assertIn("Working directory: /tmp/workspace", out)
        self.assertTrue(out.startswith("# Tool policy"))

    def test_skill_list_is_emptied(self):
        out = self.scrub("a <available_skills>\n<skill>secret</skill>\n</available_skills> b")
        self.assertEqual(out, "a <available_skills></available_skills> b")

    def test_names_inside_other_words_survive(self):
        self.assertEqual(self.scrub("malice alice_x"), "malice alice_x")

    def test_short_user_name_is_not_replaced_everywhere(self):
        self.assertEqual(self.capture.scrub_text("the bo said", "", "bo", ""), "the bo said")

    def test_non_strings_are_kept(self):
        self.assertEqual(self.scrub({"n": 1, "ok": True, "v": None}), {"n": 1, "ok": True, "v": None})


if __name__ == "__main__":
    unittest.main()
