import unittest
from unittest import mock

from stealthprint.client import ChatClient


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
