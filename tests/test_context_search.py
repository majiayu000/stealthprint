import contextlib
import io
import unittest
from unittest import mock

from stealthprint import layers
from stealthprint.client import ChatClient


def response(prompt_tokens):
    return {"usage": {"prompt_tokens": prompt_tokens}}, None


class ContextSearchTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatClient(model="test-model", base_url="https://example.invalid/v1",
                                 api_key="dummy-test-key")

    def test_failed_initial_probe_returns_no_verified_metrics(self):
        errors = [{"http": 413, "body": "input too large"},
                  {"http": 503, "body": "upstream unavailable"},
                  {"http": 429, "body": "rate limited"},
                  {"http": None, "body": "request timed out"}]
        for error in errors:
            with self.subTest(error=error):
                with mock.patch.object(self.client, "chat", return_value=(None, error)) as chat:
                    result = layers.context_search(self.client, verbose=False)
                self.assertIsNone(result["max_verified_chars"])
                self.assertIsNone(result["max_verified_prompt_tokens"])
                self.assertEqual(result["error"], error)
                self.assertEqual(result["history"], [])
                chat.assert_called_once()
                text = chat.call_args.args[0][0]["content"]
                self.assertEqual(text, "Answer with the single word OK and nothing else.\n\n"
                                 + layers.FILL * (10_000 // len(layers.FILL)))

    def test_initial_failure_is_visible_without_success_summary(self):
        error = {"http": 503, "body": "initial probe unavailable"}
        with mock.patch.object(self.client, "chat", return_value=(None, error)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = layers.context_search(self.client, max_bytes=10_000)
        self.assertIsNone(result["max_verified_chars"])
        self.assertNotIn("max verified:", output.getvalue())
        self.assertIn(error["body"], output.getvalue())

    def test_final_probe_failure_does_not_report_verified_metrics(self):
        error = {"http": 503, "body": "confirmation unavailable"}
        with mock.patch.object(self.client, "chat", side_effect=[
                response(2500), (None, error)]) as chat:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = layers.context_search(self.client, max_bytes=10_000)
        self.assertEqual(chat.call_count, 2)
        self.assertIsNone(result["max_verified_chars"])
        self.assertIsNone(result["max_verified_prompt_tokens"])
        self.assertEqual(result["error"], error)
        self.assertEqual(result["history"], [])
        self.assertNotIn("max verified:", output.getvalue())
        self.assertIn(error["body"], output.getvalue())

    def test_failed_final_confirmation_preserves_search_history(self):
        error = {"http": 429, "body": "confirmation rate limited"}
        with mock.patch.object(self.client, "chat", side_effect=[
                response(2500), response(5000), (None, error)]) as chat:
            result = layers.context_search(self.client, max_bytes=30_000,
                                           min_step=15_000, verbose=False)
        self.assertEqual(chat.call_count, 3)
        self.assertIsNone(result["max_verified_chars"])
        self.assertIsNone(result["max_verified_prompt_tokens"])
        self.assertEqual(result["error"], error)
        self.assertEqual(result["history"], [
            {"chars": 20_000, "ok": True, "prompt_tokens": 5000}])

    def test_boundary_search_returns_successfully_confirmed_counts(self):
        error = {"http": 413, "body": "input too large"}
        with mock.patch.object(self.client, "chat", side_effect=[
                response(2500), response(7500), (None, error), response(7600)]) as chat:
            result = layers.context_search(self.client, max_bytes=50_000,
                                           min_step=10_000, verbose=False)
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(result, {
            "max_verified_chars": 30_000, "max_verified_prompt_tokens": 7600,
            "history": [{"chars": 30_000, "ok": True, "prompt_tokens": 7500},
                        {"chars": 40_000, "ok": False, "prompt_tokens": None}]})
        self.assertEqual(chat.call_args_list[1].args, chat.call_args_list[-1].args)

    def test_successful_floor_accepts_zero_token_count(self):
        with mock.patch.object(self.client, "chat", side_effect=[
                response(1234), response(0)]) as chat:
            result = layers.context_search(self.client, max_bytes=10_000, verbose=False)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(result, {"max_verified_chars": 10_000,
                                  "max_verified_prompt_tokens": 0, "history": []})


if __name__ == "__main__":
    unittest.main()
