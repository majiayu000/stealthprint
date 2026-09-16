import unittest

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


if __name__ == "__main__":
    unittest.main()
