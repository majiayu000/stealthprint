import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from stealthprint import probes_extra


class FakeClient:
    """Offline chat/prompt_tokens mock; per-key call counters pick a scripted
    response from MODES (majority) or OUTLIER (first call per key)."""

    def __init__(self, modes, outliers=None):
        self.modes = modes          # text -> majority prompt_tokens
        self.outliers = outliers or {}  # text -> value returned on 1st call
        self._calls = {}
        self._lock = threading.Lock()
        self.chat_calls = []        # (messages, extra) audit trail

    def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        text = messages[0]["content"]
        self.chat_calls.append((list(messages), extra))
        with self._lock:
            n = self._calls.get(text, 0)
            self._calls[text] = n + 1
        pt = self.outliers.get(text, self.modes[text]) if n == 0 else self.modes[text]
        return {"usage": {"prompt_tokens": pt}}, None

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        d, err = self.chat(messages, max_tokens=max_tokens, extra=extra)
        if err:
            return None, err
        return d["usage"]["prompt_tokens"], None


class ModeAndDistTests(unittest.TestCase):
    def test_mode_filters_none_and_counts(self):
        mode, dist = probes_extra._mode_and_dist([33, 21, 33, None, 33])
        self.assertEqual(mode, 33)
        self.assertEqual(dist, {21: 1, 33: 3})

    def test_all_none(self):
        self.assertEqual(probes_extra._mode_and_dist([None, None]), (None, {}))


class TokenizerRepeatsTests(unittest.TestCase):
    PROBES = {"base": "hello ", "probes": [["p1", "world"], ["p2", "foo"]]}

    def test_unstable_samples_do_not_reach_ranking(self):
        client = FakeClient({"hello ":33,"hello world":45,"hello foo":38}, {"hello ":21})
        with mock.patch.object(probes_extra, "load_local_tokenizers", return_value={}), mock.patch("stealthprint.layers.tokenizer_differential") as diff:
            r = probes_extra.tokenizer_repeats(client, probes=self.PROBES, repeats=3, verbose=False)
        diff.assert_not_called()
        self.assertEqual(r["distribution"]["base"], {21:1,33:2})
        self.assertTrue(r["count_variation"])
        self.assertEqual(r["ranking"], {})

    def test_stable_samples_are_interleaved_and_ranked(self):
        client = FakeClient({"hello ":33,"hello world":45,"hello foo":38})
        with mock.patch.object(probes_extra, "load_local_tokenizers", return_value={}), mock.patch("stealthprint.layers.tokenizer_differential", return_value={"ranking":{"test":{}}}) as diff:
            r = probes_extra.tokenizer_repeats(client, probes=self.PROBES, repeats=2, verbose=False)
        self.assertFalse(r["count_variation"])
        self.assertEqual(diff.call_args.kwargs["api_delta"], {"p1":12,"p2":5})
        self.assertEqual([call[0][0]["content"] for call in client.chat_calls], ["hello ","hello world","hello foo"] * 2)
        self.assertEqual(r["status"], "stable_sample")

    def test_dependency_failure_precedes_requests(self):
        client = mock.Mock()
        with mock.patch.object(probes_extra, "load_local_tokenizers", side_effect=RuntimeError("missing dependency")):
            with self.assertRaises(RuntimeError):
                probes_extra.tokenizer_repeats(client, probes=self.PROBES)
        client.prompt_tokens.assert_not_called()

    def test_failed_samples_preserve_each_error(self):
        client = mock.Mock()
        error = {"http":429,"body":"busy"}
        client.prompt_tokens.return_value = None, error
        with mock.patch.object(probes_extra, "load_local_tokenizers", return_value={}):
            r = probes_extra.tokenizer_repeats(client, probes=self.PROBES, repeats=2, verbose=False)
        self.assertEqual(len(r["failures"]), 6)
        self.assertEqual(r["failures"][0]["error"], error)
        self.assertEqual(r["status"], "incomplete")
        self.assertEqual(r["ranking"], {})


class _CharTok:
    """Fake HF-style tokenizer: 1 token per character."""

    def encode(self, s, add_special_tokens=False):
        return SimpleNamespace(ids=list(s))


class EncodeLenTests(unittest.TestCase):
    def test_tiktoken_branch_selected_by_name_suffix(self):
        class FakeTik:
            def encode(self, s, disallowed_special=()):  # tiktoken API
                return [0] * len(s)

        self.assertEqual(probes_extra.encode_len("cl100k_base", FakeTik(), "abc"), 3)
        self.assertEqual(probes_extra.encode_len("o200k_base", FakeTik(), "ab"), 2)
        self.assertEqual(probes_extra.encode_len("llama3", _CharTok(), "abc"), 3)


class EchoVerifyTests(unittest.TestCase):
    def _patch_toks(self):
        return mock.patch.object(probes_extra, "load_local_tokenizers",
                                 return_value={"chars": _CharTok()})

    def test_offsets_against_local_vocabs(self):
        probes = {"base": "b ", "probes": [["p1", "hola"], ["p2", "bonjour"]]}

        class Echo:
            def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
                text = messages[0]["content"][len(probes_extra._ECHO_PROMPT):]
                return ({"choices": [{"message": {"content": text}}],
                         "usage": {"completion_tokens": len(text) + 4}}, None)

        with self._patch_toks():
            r = probes_extra.echo_verify(Echo(), probes=probes, verbose=False)
        for name, text in probes["probes"]:
            row = r["echoes"][name]
            self.assertTrue(row["echo_exact"])
            self.assertEqual(row["offsets"], {"chars": 4})

    def test_error_response_recorded(self):
        probes = {"base": "b ", "probes": [["p1", "x"]]}

        class Err:
            def chat(self, *a, **k):
                return {}, {"http": 429, "body": "busy"}

        with self._patch_toks():
            r = probes_extra.echo_verify(Err(), probes=probes, verbose=False)
        self.assertIn("error", r["echoes"]["p1"])


class ToolsProbeTests(unittest.TestCase):
    def _schema_client(self, baseline, tools):
        client = mock.Mock()
        client.prompt_tokens.return_value = baseline
        client.chat.side_effect = [tools] + [
            ({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}, None)
        ] * 3
        return client

    def test_failed_baseline_preserves_error_and_tool_choices(self):
        for err in ({"http": 500, "body": "upstream failed"},
                    {"http": 429, "body": "rate limited"},
                    {"http": None, "body": "timed out"}):
            with self.subTest(err=err):
                client = self._schema_client(
                    (None, err), ({"usage": {"prompt_tokens": 400}}, None))
                r = probes_extra.tools_probe(client, verbose=False)
                self.assertIsNone(r["schema_overhead"])
                self.assertEqual(r["baseline_error"], err)
                for choice in ("none", "auto", "required"):
                    self.assertEqual(r["tool_choice_" + choice]["finish"], "stop")
                self.assertEqual(client.chat.call_count, 4)

    def test_missing_baseline_count_is_not_zero(self):
        client = self._schema_client(
            (None, None), ({"usage": {"prompt_tokens": 400}}, None))
        r = probes_extra.tools_probe(client, verbose=False)
        self.assertIsNone(r["schema_overhead"])

    def test_zero_baseline_count_is_valid(self):
        client = self._schema_client(
            (0, None), ({"usage": {"prompt_tokens": 400}}, None))
        r = probes_extra.tools_probe(client, verbose=False)
        self.assertEqual(r["schema_overhead"], 400)
        self.assertNotIn("baseline_error", r)

    def test_unavailable_tools_count_is_not_subtracted(self):
        for tools in ((None, {"http": 503, "body": "unavailable"}),
                      ({}, None), ({"usage": {"prompt_tokens": None}}, None)):
            with self.subTest(tools=tools):
                client = self._schema_client((10, None), tools)
                r = probes_extra.tools_probe(client, verbose=False)
                self.assertIsNone(r["schema_overhead"])

    def test_schema_overhead_and_calls(self):
        class ToolClient:
            def __init__(self):
                self.seen = []

            def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
                return 10, None

            def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
                self.seen.append(extra or {})
                if "tools" in (extra or {}) and "tool_choice" not in (extra or {}):
                    return ({"usage": {"prompt_tokens": 35}}, None)
                choice = (extra or {}).get("tool_choice")
                if choice == "required":
                    msg = {"tool_calls": [
                        {"function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}
                    return ({"choices": [{"message": msg, "finish_reason": "tool_calls"}]}, None)
                return ({"choices": [{"message": {"content": "Sure."},
                                      "finish_reason": "stop"}]}, None)

        r = probes_extra.tools_probe(ToolClient(), verbose=False)
        self.assertEqual(r["schema_overhead"], 25)
        self.assertEqual(r["tool_choice_required"]["finish"], "tool_calls")
        self.assertEqual(r["tool_choice_required"]["n_calls"], 1)
        self.assertEqual(r["tool_choice_required"]["calls"][0]["name"], "get_weather")
        self.assertEqual(r["tool_choice_auto"]["n_calls"], 0)

    def test_error_choice_recorded(self):
        class ErrClient:
            def prompt_tokens(self, *a, **k):
                return 10, None

            def chat(self, *a, **k):
                return {}, {"http": 400, "body": "x"}

        r = probes_extra.tools_probe(ErrClient(), verbose=False)
        self.assertIn("error", r["tool_choice_none"])


class WrapperTurnsTests(unittest.TestCase):
    class TurnClient:
        def __init__(self, fn):
            self.fn = fn

        def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
            content_tokens = sum(len(message["content"]) for message in messages)
            return content_tokens + self.fn(len(messages)), None

    def test_constant_per_turn(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 3 * n),
                                       turns=4, verbose=False)
        # turn 1 has no predecessor delta; each later turn adds user+assistant
        self.assertEqual([row["prompt_tokens"] for row in r["turns"]], [10, 25, 40, 55])
        self.assertEqual([row["delta"] for row in r["turns"]], [None, 15, 15, 15])
        self.assertTrue(r["per_turn_constant"])

    def test_non_constant_per_turn(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 3 * n * n),
                                       turns=4, verbose=False)
        self.assertEqual([row["delta"] for row in r["turns"]], [None, 33, 57, 81])
        self.assertFalse(r["per_turn_constant"])

    def test_first_delta_is_compared(self):
        for turns in (3, 4):
            with self.subTest(turns=turns):
                client = self.TurnClient(lambda n: 3 * n + (5 if n > 1 else 0))
                r = probes_extra.wrapper_turns(client, turns=turns, verbose=False)
                self.assertEqual([row["delta"] for row in r["turns"]],
                                 [None, 20] + [15] * (turns - 2))
                self.assertIs(r["per_turn_constant"], False)

    def test_three_turns_provide_two_comparable_deltas(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 3 * n),
                                       turns=3, verbose=False)
        self.assertEqual([row["delta"] for row in r["turns"]], [None, 15, 15])
        self.assertIs(r["per_turn_constant"], True)

    def test_too_few_turns_is_none(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 3 * n),
                                       turns=2, verbose=False)
        self.assertIsNone(r["per_turn_constant"])

    def test_failed_turn_stops_without_a_delta(self):
        client = mock.Mock()
        client.prompt_tokens.side_effect = [(10, None), (None, {"http": 503, "body": "busy"})]
        r = probes_extra.wrapper_turns(client, turns=4, verbose=False)
        self.assertEqual(r, {"turns": [
            {"turn": 1, "prompt_tokens": 10, "delta": None},
            {"turn": 2, "prompt_tokens": None, "delta": None, "error": {"http":503,"body":"busy"}},
        ], "per_turn_constant": None})
        self.assertEqual(client.prompt_tokens.call_count, 2)


if __name__ == "__main__":
    unittest.main()
