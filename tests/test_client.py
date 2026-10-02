import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from stealthprint.client import ChatClient
from stealthprint.cli import main


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data": [{"id": "m"}]}')

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.base_url = "http://127.0.0.1:%d/v1" % self.server.server_port

        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.auth_path = Path(fixture.name) / "auth.json"
        self.auth_path.write_text(json.dumps({
            "opencode-go": {"key": "dummy-opencode-go"},
            "opencode": {"key": "dummy-opencode"},
        }), encoding="utf-8")
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        expanduser = mock.patch("stealthprint.client.os.path.expanduser",
                                return_value=str(self.auth_path))
        expanduser.start()
        self.addCleanup(expanduser.stop)

    def test_opencode_credentials_are_not_sent_without_explicit_key(self):
        for base_source in ("argument", "environment"):
            with self.subTest(base_source=base_source):
                kwargs = {"base_url": self.base_url} if base_source == "argument" else {}
                with mock.patch.dict(os.environ, {"STEALTHPRINT_BASE_URL": self.base_url}):
                    try:
                        client = ChatClient(model="m", **kwargs)
                    except ValueError as error:
                        self.assertEqual(str(error),
                                         "no api_key (pass api_key= or set STEALTHPRINT_API_KEY)")
                    else:
                        # On the vulnerable version, prove the leak on the wire.
                        client.list_models()
                        self.fail("client accepted an unrelated OpenCode credential")
        self.assertEqual(self.requests, [])

    def test_explicit_key_takes_precedence_over_environment_and_opencode(self):
        with mock.patch.dict(os.environ, {"STEALTHPRINT_API_KEY": "dummy-env"}):
            client = ChatClient(model="m", base_url=self.base_url, api_key="dummy-explicit")
        self.assertEqual(client.list_models(), (["m"], None))
        self.assertEqual(self.requests, [("/v1/models", "Bearer dummy-explicit")])

    def test_environment_key_is_sent_to_selected_endpoint(self):
        with mock.patch.dict(os.environ, {"STEALTHPRINT_API_KEY": "dummy-env",
                                          "STEALTHPRINT_BASE_URL": self.base_url}):
            client = ChatClient(model="m")
        self.assertEqual(client.list_models(), (["m"], None))
        self.assertEqual(self.requests, [("/v1/models", "Bearer dummy-env")])

    def test_missing_base_url_keeps_existing_error(self):
        with self.assertRaisesRegex(ValueError, "^no base_url"):
            ChatClient(model="m", api_key="dummy-explicit")
        self.assertEqual(self.requests, [])

    def test_cli_missing_key_exits_before_request(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--model", "m", "--base-url", self.base_url, "catalog"])
        self.assertEqual(raised.exception.code,
                         "no api_key (pass api_key= or set STEALTHPRINT_API_KEY)")
        self.assertEqual(self.requests, [])


class SessionHeaderTests(unittest.TestCase):
    def _make(self, **kw):
        kw.setdefault("model", "m")
        kw.setdefault("base_url", "https://x/v1")
        kw.setdefault("api_key", "sk-test")
        return ChatClient(**kw)

    def test_headers_include_session_id(self):
        h = self._make()._headers()
        self.assertIn("x-session-id", h)
        self.assertEqual(len(h["x-session-id"]), 36)  # uuid4 string form

    def test_explicit_session_id_is_used(self):
        c = self._make(session_id="fixed-session")
        self.assertEqual(c._headers()["x-session-id"], "fixed-session")

    def test_session_id_stable_within_client(self):
        c = self._make()
        self.assertEqual(c._headers()["x-session-id"], c._headers()["x-session-id"])

    def test_two_clients_get_distinct_sessions(self):
        self.assertNotEqual(self._make().session_id, self._make().session_id)


class PromptTokenTests(unittest.TestCase):
    def test_missing_prompt_count_returns_error(self):
        client = ChatClient(model="m", base_url="https://x/v1", api_key="sk-test")
        for response in ({}, {"usage": {}}, {"usage": {"completion_tokens": 1}},
                         {"usage": {"prompt_tokens": None}}):
            with self.subTest(response=response):
                with mock.patch.object(client, "chat", return_value=(response, None)):
                    pt, err = client.prompt_tokens([{"role": "user", "content": "hi"}])
                self.assertIsNone(pt)
                self.assertEqual(err, {"http": 200, "body": "no prompt_tokens in usage"})

    def test_zero_count_and_request_error_are_preserved(self):
        client = ChatClient(model="m", base_url="https://x/v1", api_key="sk-test")
        error = {"http": 500, "body": "upstream failed"}
        for response, expected in ((({"usage": {"prompt_tokens": 0}}, None), (0, None)),
                                   ((None, error), (None, error))):
            with self.subTest(response=response):
                with mock.patch.object(client, "chat", return_value=response):
                    self.assertEqual(client.prompt_tokens([]), expected)


if __name__ == "__main__":
    unittest.main()
