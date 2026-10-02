import unittest
from unittest import mock

from stealthprint import layers
from stealthprint.client import ChatClient


def chat_response(prompt_tokens, answer="red"):
    return {"usage": {"prompt_tokens": prompt_tokens},
            "choices": [{"message": {"content": answer}}]}, None


class VisionTruthTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatClient(model="test-model", base_url="https://example.invalid/v1",
                                 api_key="dummy-test-key")

    def test_failed_control_stops_before_image_requests(self):
        errors = [{"http": 500, "body": "control upstream failed"},
                  {"http": 429, "body": "control rate limited"},
                  {"http": None, "body": "control timed out"}]
        for error in errors:
            with self.subTest(error=error):
                # The image would succeed, exposing the original pt - None crash.
                with mock.patch.object(self.client, "chat", side_effect=[
                        (None, error), chat_response(25)]) as chat:
                    with mock.patch.object(layers, "png_b64", return_value="image") as image:
                        with self.assertRaisesRegex(RuntimeError, "no-image control request failed") as ctx:
                            layers.vision_truth(self.client, verbose=False)
                self.assertIn(str(error), str(ctx.exception))
                chat.assert_called_once()
                self.assertIsInstance(chat.call_args.args[0][0]["content"], str)
                image.assert_not_called()

    def test_successful_control_preserves_deltas_and_colors(self):
        responses = [chat_response(10), chat_response(25, "red"),
                     chat_response(30, "blue"), chat_response(40, "red"),
                     chat_response(50, "blue")]
        with mock.patch.object(self.client, "chat", side_effect=responses) as chat:
            out = layers.vision_truth(self.client, verbose=False)
        self.assertEqual(chat.call_count, 5)
        self.assertEqual(out["no_image_control_prompt_tokens"], 10)
        self.assertEqual([row["delta"] for row in out["probes"]], [15, 20, 30, 40])
        self.assertEqual([row["attempts"] for row in out["probes"]], [1, 1, 1, 1])
        self.assertEqual(out["color_correct"], "4/4")
        self.assertEqual(out["failures"], [])

    def test_zero_token_control_remains_valid(self):
        with mock.patch.object(self.client, "chat", side_effect=[
                chat_response(0), chat_response(25), chat_response(25)]):
            out = layers.vision_truth(self.client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertEqual(out["no_image_control_prompt_tokens"], 0)
        self.assertEqual([row["delta"] for row in out["probes"]], [25, 25])

    def test_image_failures_preserve_retries_and_partial_results(self):
        error = {"http": 429, "body": "image rate limited"}
        with mock.patch.object(self.client, "chat", side_effect=[
                chat_response(10), (None, error), chat_response(25),
                (None, error), (None, error), (None, error)]) as chat:
            out = layers.vision_truth(self.client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertEqual(chat.call_count, 6)
        self.assertEqual(out["no_image_control_prompt_tokens"], 10)
        self.assertEqual(len(out["probes"]), 1)
        self.assertEqual(out["probes"][0]["delta"], 15)
        self.assertEqual(out["probes"][0]["attempts"], 2)
        self.assertEqual(out["color_correct"], "1/1")
        self.assertEqual(len(out["failures"]), 4)
        self.assertEqual([row["attempt"] for row in out["failures"]], [1, 1, 2, 3])
        for failure in out["failures"]:
            self.assertEqual(failure["http"], error["http"])
            self.assertEqual(failure["body"], error["body"])


if __name__ == "__main__":
    unittest.main()
