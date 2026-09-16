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
