"""Unit tests for ChatClient.prompt_tokens malformed-JSON handling."""

import unittest
from unittest.mock import patch

from stealthprint.client import ChatClient


class TestPromptTokens(unittest.TestCase):
    def setUp(self):
        self.client = ChatClient(model="m", base_url="http://example.test", api_key="k")

    def test_missing_usage_returns_structured_err(self):
        with patch.object(self.client, "chat", return_value=({"choices": []}, None)):
            pt, err = self.client.prompt_tokens([{"role": "user", "content": "hi"}])
        self.assertIsNone(pt)
        self.assertIsInstance(err, dict)
        self.assertIn("usage.prompt_tokens", err["body"])

    def test_missing_prompt_tokens_returns_structured_err(self):
        with patch.object(self.client, "chat", return_value=({"usage": {}}, None)):
            pt, err = self.client.prompt_tokens([{"role": "user", "content": "hi"}])
        self.assertIsNone(pt)
        self.assertIsInstance(err, dict)
        self.assertIn("usage.prompt_tokens", err["body"])

    def test_success_returns_tokens(self):
        with patch.object(
            self.client, "chat",
            return_value=({"usage": {"prompt_tokens": 12}}, None),
        ):
            pt, err = self.client.prompt_tokens([{"role": "user", "content": "hi"}])
        self.assertEqual(pt, 12)
        self.assertIsNone(err)

    def test_chat_error_passthrough(self):
        http_err = {"http": 500, "body": "boom"}
        with patch.object(self.client, "chat", return_value=(None, http_err)):
            pt, err = self.client.prompt_tokens([{"role": "user", "content": "hi"}])
        self.assertIsNone(pt)
        self.assertEqual(err, http_err)


if __name__ == "__main__":
    unittest.main()
