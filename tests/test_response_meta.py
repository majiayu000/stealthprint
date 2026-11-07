"""Response metadata capture and request budget; no model API calls."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from stealthprint.cli import main
from stealthprint.client import ChatClient, response_body_meta, response_headers
from stealthprint.survey import BudgetExhausted, RequestBudget

BODY = {"id": "chatcmpl-SECRETID123", "model": "real-upstream", "usage": {"prompt_tokens": 5},
        "choices": [{"finish_reason": "length", "message": {"content": "x"}}]}


class Handler(BaseHTTPRequestHandler):
    status = 200

    def version_string(self):
        return "edge-1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(self.status)
        self.send_header("Server", "edge-1")
        self.send_header("Set-Cookie", "session=COOKIESECRET")
        self.send_header("X-Request-Id", "TRACESECRET")
        self.send_header("X-Zen-Model", "upstream-7")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(BODY).encode())

    def log_message(self, *args):
        pass


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def client(self, **kw):
        return ChatClient("m", "http://127.0.0.1:%d" % self.srv.server_port, "dummy-secret", **kw)

    def test_record_keeps_header_names_and_drops_sensitive_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r.jsonl"
            self.client(record_path=path).prompt_tokens([{"role": "user", "content": "hi"}])
            row = json.loads(path.read_text())
        headers = row["response_headers"]
        self.assertEqual(headers["server"], "edge-1")
        self.assertEqual(headers["x-zen-model"], "upstream-7")
        self.assertIsNone(headers["set-cookie"])
        self.assertIsNone(headers["x-request-id"])
        self.assertNotIn("COOKIESECRET", json.dumps(headers))
        self.assertNotIn("TRACESECRET", json.dumps(headers))

    def test_meta_aggregates_without_full_ids_or_secrets(self):
        c = self.client()
        for _ in range(2):
            c.prompt_tokens([{"role": "user", "content": "hi"}])
        meta = c.response_meta()
        self.assertEqual(meta["responses"], 2)
        self.assertEqual(meta["id_prefixes"], {"chatcmpl-": 2})
        self.assertEqual(meta["models"], {"real-upstream": 2})
        self.assertEqual(meta["finish_reasons"], {"length": 2})
        self.assertEqual(meta["header_names"]["set-cookie"], 2)
        dumped = json.dumps(meta)
        for secret in ("SECRETID123", "COOKIESECRET", "TRACESECRET", "dummy-secret"):
            self.assertNotIn(secret, dumped)

    def test_http_error_responses_are_counted(self):
        with mock.patch.object(Handler, "status", 500):
            c = self.client()
            _, err = c.request("POST", "/x", body={})
        self.assertEqual(err["http"], 500)
        self.assertEqual(c.response_meta()["header_names"]["server"], 1)

    def test_transport_failure_adds_nothing(self):
        c = ChatClient("m", "http://127.0.0.1:1", "k", timeout=1)
        c.request("POST", "/x", body={})
        self.assertEqual(c.response_meta()["responses"], 0)

    def test_unprefixed_id_is_not_stored(self):
        self.assertEqual(response_body_meta({"id": "12345abcdef"})["id_prefix"], "(no prefix)")
        self.assertEqual(response_body_meta({"id": "msg_01ABC"})["id_prefix"], "msg_")
        self.assertEqual(response_body_meta(None), {})
        self.assertEqual(response_headers(None), {})


class MatchExitTests(unittest.TestCase):
    def run_match(self, target, peer):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "lib"
            lib.mkdir()
            (lib / "p.json").write_text(json.dumps(peer))
            path = Path(d) / "t.json"
            path.write_text(json.dumps(target))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["match", str(path), "--library", str(lib), "--json"])
        return code, json.loads(out.getvalue())

    L1 = {"p%d" % i: i for i in range(8)}

    def test_exit_codes(self):
        cases = [(self.L1, self.L1, 0, "match"), (self.L1, dict(self.L1, p0=99), 3, "no_match"),
                 ({"x": 1}, {"x": 1}, 4, "insufficient")]
        for a, b, code, status in cases:
            got, payload = self.run_match({"model": "t", "l1_delta": a}, {"model": "p", "l1_delta": b})
            self.assertEqual((got, payload["status"]), (code, status))

    def test_empty_library_is_insufficient(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.json"
            path.write_text(json.dumps({"model": "t", "l1_delta": self.L1}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["match", str(path), "--library", d, "--json"]), 4)


class BudgetTests(unittest.TestCase):
    def test_cap_holds_across_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b")
            first = RequestBudget(2, path)
            first.take(), first.take()
            with self.assertRaises(BudgetExhausted):
                first.take()
            with self.assertRaises(BudgetExhausted):
                RequestBudget(2, path).take()
            self.assertEqual(RequestBudget(3, path).spent, 2)

    def test_blind_eval_requires_a_cap(self):
        spec = importlib.util.spec_from_file_location(
            "blind_eval_cap", os.path.join(os.path.dirname(__file__), "..", "scripts", "blind_eval.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            module.main(["run", "--out", "x.jsonl"])
