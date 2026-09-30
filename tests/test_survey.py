import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from stealthprint import survey


class _CharTok:
    """Fake HF-style tokenizer: 1 token per character."""

    def encode(self, s, add_special_tokens=False):
        return SimpleNamespace(ids=list(s))


PROBES = {"base": "b ", "probes": [["p1", "x"], ["p2", "yy"]]}
PROBE_NAMES = ["p1", "p2"]


class FakeModelClient:
    """One model's endpoint. ok: prompt_tokens = len(text) + pad;
    chat_fail: /chat fails with `err`, /responses answers (Muse-style);
    dead: both paths fail."""

    def __init__(self, spec):
        self.spec = spec  # dict: kind, pad, err

    def chat(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        if self.spec["kind"] in ("chat_fail", "dead"):
            return None, self.spec["err"]
        text = messages[0]["content"]
        return {"usage": {"prompt_tokens": len(text) + self.spec["pad"]}}, None

    def prompt_tokens(self, messages, max_tokens=1, extra=None, timeout=None, model=None):
        d, err = self.chat(messages, max_tokens=max_tokens)
        if err:
            return None, err
        return d["usage"]["prompt_tokens"], None

    def request(self, method, path, body=None, timeout=None):
        if self.spec["kind"] in ("ok", "dead") or path != "/responses":
            return None, self.spec["err"]
        return {"usage": {"input_tokens": len(body["input"]) + self.spec["pad"]}}, None


def make_factory(specs):
    def factory(model):
        return FakeModelClient(specs[model])
    return factory


def patch_env():
    return (mock.patch("stealthprint.survey.load_probes", return_value=PROBES),
            mock.patch("stealthprint.survey.load_local_tokenizers",
                       return_value={"chars": _CharTok()}))


class ClassifyErrorTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(survey.classify_error({"http": 401, "body": "x"}), "paid_401")
        self.assertEqual(survey.classify_error({"http": 403, "body": "CreditsError"}), "paid_401")
        self.assertEqual(survey.classify_error({"http": 429, "body": "x"}), "rate_limited_429")
        self.assertEqual(survey.classify_error({"http": 500, "body": "x"}), "upstream_5xx")
        self.assertEqual(survey.classify_error({"http": 400, "body": "x"}), "bad_request_400")
        self.assertEqual(survey.classify_error({"http": None, "body": "timed out"}),
                         "timeout_or_network")
        self.assertEqual(survey.classify_error({"http": 404, "body": "x"}), "other_http_404")


class MeasureResponsesTests(unittest.TestCase):
    def make_client(self, chat_error=None):
        client = mock.Mock()
        client.prompt_tokens.return_value = (
            None, chat_error or {"http": 404, "body": "chat unsupported"})
        return client

    def measure(self, client, retries=3, delay=0):
        return survey._measure(lambda model: client, "m", "b ",
                               [("p1", "b x")], retries, delay)

    def test_responses_baseline_recovers_after_transient_failure(self):
        client = self.make_client()
        client.request.side_effect = [
            (None, {"http": 503, "body": "temporary"}),
            ({"usage": {"input_tokens": 10}}, None),
            ({"usage": {"input_tokens": 12}}, None),
        ]
        row, cls, detail = self.measure(client)
        self.assertIsNone(cls)
        self.assertIsNone(detail)
        self.assertEqual(row, {"mode": "responses", "base_api": 10,
                               "deltas": {"p1": 2}, "fails": 0})
        self.assertEqual(client.prompt_tokens.call_count, 4)
        self.assertEqual([call.kwargs["body"]["input"]
                          for call in client.request.call_args_list],
                         ["b ", "b ", "b x"])

    def test_responses_baseline_exhausts_exact_retry_budget(self):
        for retries in (0, 3):
            with self.subTest(retries=retries):
                client = self.make_client()
                error = {"http": 503, "body": "still unavailable"}
                client.request.return_value = (None, error)
                row, cls, detail = self.measure(client, retries=retries)
                self.assertIsNone(row)
                self.assertEqual(cls, "other_http_404")
                self.assertEqual(detail["responses"], error)
                self.assertEqual(client.request.call_count, retries + 1)

    def test_responses_baseline_retries_missing_usage_and_accepts_zero(self):
        client = self.make_client()
        client.request.side_effect = [
            ({"usage": {}}, None),
            ({"usage": {"input_tokens": 0}}, None),
            ({"usage": {"input_tokens": 2}}, None),
        ]
        row, cls, detail = self.measure(client, retries=1)
        self.assertIsNone(cls)
        self.assertIsNone(detail)
        self.assertEqual(row["base_api"], 0)
        self.assertEqual(row["deltas"], {"p1": 2})

    def test_responses_probe_keeps_retries_and_delay(self):
        client = self.make_client()
        client.request.side_effect = [
            ({"usage": {"input_tokens": 10}}, None),
            (None, {"http": 503, "body": "temporary"}),
            ({"usage": {"input_tokens": 12}}, None),
        ]
        with mock.patch("stealthprint.survey.time.sleep") as sleep:
            row, cls, detail = self.measure(client, retries=1, delay=0.25)
        self.assertEqual(row["deltas"], {"p1": 2})
        self.assertEqual(row["fails"], 0)
        self.assertIsNone(cls)
        self.assertIsNone(detail)
        self.assertEqual(sleep.call_args_list, [mock.call(0.25)] * 3)
        self.assertEqual(client.request.call_count, 3)

    def test_both_endpoint_errors_choose_credit_or_rate_limit(self):
        cases = [
            ({"http": 500, "body": "chat down"},
             {"http": 401, "body": "CreditsError"}, "paid_401"),
            ({"http": 500, "body": "chat down"},
             {"http": 403, "body": "CreditsError"}, "paid_401"),
            ({"http": None, "body": "timeout"},
             {"http": 429, "body": "too many requests"}, "rate_limited_429"),
            ({"http": 500, "body": "chat down"},
             {"http": 429, "body": "too many requests"}, "rate_limited_429"),
            ({"http": 429, "body": "too many requests"},
             {"http": 401, "body": "CreditsError"}, "paid_401"),
            ({"http": 401, "body": "CreditsError"},
             {"http": 429, "body": "too many requests"}, "paid_401"),
            ({"http": 429, "body": "too many requests"},
             {"http": 503, "body": "responses down"}, "rate_limited_429"),
            ({"http": 503, "body": "chat down"},
             {"http": 404, "body": "responses unsupported"}, "upstream_5xx"),
        ]
        for chat_error, responses_error, expected in cases:
            with self.subTest(chat=chat_error, responses=responses_error):
                client = self.make_client(chat_error)
                client.request.return_value = (None, responses_error)
                row, cls, detail = self.measure(client, retries=0)
                self.assertIsNone(row)
                self.assertEqual(cls, expected)
                self.assertEqual(detail, {"chat": chat_error,
                                          "responses": responses_error})


class RunSurveyTests(unittest.TestCase):
    def setUp(self):
        # ok model: base "b " (2 chars) -> pt 2+8=10; probes add exact char deltas
        self.specs = {
            "m-ok": {"kind": "ok", "pad": 8, "err": None},
            "m-paid": {"kind": "dead", "pad": 0, "err": {"http": 401, "body": "CreditsError"}},
            "m-muse": {"kind": "chat_fail", "pad": 5, "err": {"http": 500, "body": "boom"}},
        }
        self.models = list(self.specs)

    def run_survey(self, **kw):
        p1, p2 = patch_env()
        with p1, p2:
            return survey.run_survey(make_factory(self.specs), self.models,
                                     probe_names=PROBE_NAMES, delay=0, verbose=False, **kw)

    def test_classification_and_measurement(self):
        r = self.run_survey()
        self.assertEqual(r["catalog_size"], 3)
        self.assertEqual(r["classes"], {"paid_401": ["m-paid"]})
        ok = r["measured"]["m-ok"]
        # base 2 chars +8 pad = 10; deltas are exact char counts -> chars 2/2
        self.assertEqual(ok["base_api"], 10)
        self.assertEqual(ok["deltas"], {"p1": 1, "p2": 2})
        self.assertEqual(ok["best"], "chars")
        self.assertEqual(ok["scores"]["chars"]["exact"], 2)
        self.assertEqual(ok["scores"]["chars"]["wrapper"], 8)

    def test_responses_fallback(self):
        r = self.run_survey()
        muse = r["measured"]["m-muse"]
        self.assertEqual(muse["mode"], "responses")
        self.assertEqual(muse["base_api"], 7)  # "b " 2 chars +5 pad
        self.assertEqual(muse["deltas"], {"p1": 1, "p2": 2})

    def test_responses_credit_wall_is_counted_and_written(self):
        client = mock.Mock()
        chat_error = {"http": 500, "body": "chat down"}
        responses_error = {"http": 401, "body": "CreditsError"}
        client.prompt_tokens.return_value = (None, chat_error)
        client.request.return_value = (None, responses_error)
        p1, p2 = patch_env()
        with tempfile.TemporaryDirectory() as d, p1, p2:
            out = os.path.join(d, "results.jsonl")
            with mock.patch("builtins.print") as printed:
                result = survey.run_survey(lambda model: client, ["m-paid"],
                                           probe_names=PROBE_NAMES, delay=0,
                                           retries=0, out_path=out)
            with open(out, encoding="utf-8") as f:
                payload = json.load(f)
        self.assertEqual(result["classes"], {"paid_401": ["m-paid"]})
        self.assertEqual(result["measured"], {})
        self.assertEqual(payload, {"model": "m-paid", "class": "paid_401",
                                   "error_detail": {"chat": chat_error,
                                                    "responses": responses_error}})
        printed.assert_any_call(survey.t("survey.summary", catalog=1,
                                        measured=0, paid=1))

    def test_filter_substring(self):
        r = self.run_survey(filter_sub="muse")
        self.assertEqual(list(r["measured"]), ["m-muse"])
        self.assertEqual(r["surveyed"], 1)

    def test_max_models_budget_guard(self):
        r = self.run_survey(max_models=1)
        self.assertEqual(r["surveyed"], 1)
        self.assertEqual(len(r["measured"]) + sum(len(v) for v in r["classes"].values()), 1)

    def test_unknown_probe_rejected(self):
        p1, p2 = patch_env()
        with p1, p2:
            with self.assertRaises(ValueError):
                survey.run_survey(make_factory(self.specs), self.models,
                                  probe_names=["nope"], delay=0, verbose=False)

    def test_resume_skips_done_models(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "results.jsonl")
            with open(out, "w", encoding="utf-8") as f:
                f.write(json.dumps({"model": "m-ok", "class": None}) + "\n")
            r = self.run_survey(out_path=out)
            self.assertNotIn("m-ok", r["measured"])
            self.assertEqual(r["surveyed"], 2)
            lines = [json.loads(line) for line in open(out, encoding="utf-8")]
            self.assertEqual(len(lines), 3)  # 1 preexisting + 2 new

    def test_out_jsonl_records_classes_too(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "results.jsonl")
            self.run_survey(out_path=out)
            rows = {}
            for line in open(out, encoding="utf-8"):
                rec = json.loads(line)
                rows[rec["model"]] = rec
            self.assertEqual(rows["m-paid"]["class"], "paid_401")
            self.assertIn("error_detail", rows["m-paid"])
            self.assertIsNone(rows["m-ok"]["class"])
            self.assertEqual(rows["m-ok"]["deltas"], {"p1": 1, "p2": 2})


if __name__ == "__main__":
    unittest.main()
