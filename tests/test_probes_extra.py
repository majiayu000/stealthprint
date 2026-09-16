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

    def _client(self):
        return FakeClient(
            modes={"hello ": 33, "hello world": 45, "hello foo": 38},
            outliers={"hello ": 21, "hello world": 99},
        )

    def test_pairs_within_majority_path(self):
        client = self._client()
        with mock.patch("stealthprint.layers.tokenizer_differential",
                        return_value={"verdict": "stub"}) as diff:
            r = probes_extra.tokenizer_repeats(client, probes=self.PROBES,
                                               repeats=3, workers=3, verbose=False)
        self.assertEqual(r["base_mode"], 33)
        self.assertTrue(r["mixed_backends"])
        self.assertEqual(r["distribution"]["base"], {21: 1, 33: 2})
        # the differential must receive majority-path values, not outliers
        _, kwargs = diff.call_args
        self.assertEqual(kwargs["base_api"], 33)
        self.assertEqual(kwargs["api_delta"], {"p1": 12, "p2": 5})

    def test_single_path_not_flagged_mixed(self):
        client = FakeClient(modes={"hello ": 33, "hello world": 45, "hello foo": 38})
        with mock.patch("stealthprint.layers.tokenizer_differential",
                        return_value={"verdict": "stub"}):
            r = probes_extra.tokenizer_repeats(client, probes=self.PROBES,
                                               repeats=2, workers=2, verbose=False)
        self.assertFalse(r["mixed_backends"])

    def test_dead_base_raises(self):
        class Dead:
            def chat(self, *a, **k):
                return {}, {"http": 500, "body": "x"}

        with self.assertRaises(RuntimeError):
            probes_extra.tokenizer_repeats(Dead(), probes=self.PROBES,
                                           repeats=1, workers=1, verbose=False)


class _CharTok:
    """Fake HF-style tokenizer: 1 token per character."""

    def encode(self, s, add_special_tokens=False):
        return SimpleNamespace(ids=list(s))


class EncodeLenTests(unittest.TestCase):
    def test_tiktoken_branch_selected_by_name_suffix(self):
        class FakeTik:
            def encode(self, s):  # tiktoken API: no add_special_tokens kwarg
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
            return self.fn(len(messages)), None

    def test_constant_per_turn(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 22 + 8 * n),
                                       turns=4, verbose=False)
        # turn 1 has no predecessor delta; each later turn adds user+assistant
        self.assertEqual([row["delta"] for row in r["turns"]], [None, 16, 16, 16])
        self.assertTrue(r["per_turn_constant"])

    def test_non_constant_per_turn(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 22 + 4 * n * n),
                                       turns=4, verbose=False)
        self.assertFalse(r["per_turn_constant"])

    def test_too_few_turns_is_none(self):
        r = probes_extra.wrapper_turns(self.TurnClient(lambda n: 22 + 8 * n),
                                       turns=2, verbose=False)
        self.assertIsNone(r["per_turn_constant"])


if __name__ == "__main__":
    unittest.main()
