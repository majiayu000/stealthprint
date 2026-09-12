"""Unit tests for vision_truth control failure and malformed probe JSON."""

import unittest
from unittest.mock import MagicMock

from stealthprint import layers


def _ok_chat(answer="red", prompt_tokens=40):
    return {
        "choices": [{"message": {"content": answer}}],
        "usage": {"prompt_tokens": prompt_tokens},
    }, None


class TestVisionTruthControl(unittest.TestCase):
    def test_control_failure_raises_runtime_error(self):
        client = MagicMock()
        client.prompt_tokens.return_value = (None, {"http": 500, "body": "control down"})
        with self.assertRaises(RuntimeError) as ctx:
            layers.vision_truth(client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertIn("no-image control request failed", str(ctx.exception))
        client.chat.assert_not_called()

    def test_malformed_probe_json_records_failure_not_keyerror(self):
        client = MagicMock()
        client.prompt_tokens.return_value = (10, None)
        # Missing usage and choices — must not KeyError / TypeError the layer.
        client.chat.return_value = ({"id": "x"}, None)
        out = layers.vision_truth(
            client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertEqual(out["no_image_control_prompt_tokens"], 10)
        self.assertEqual(out["probes"], [])
        self.assertGreater(len(out["failures"]), 0)
        bodies = " ".join(f.get("body") or "" for f in out["failures"])
        self.assertTrue(
            "missing choices" in bodies or "missing usage" in bodies,
            bodies,
        )

    def test_missing_usage_on_probe_records_failure(self):
        client = MagicMock()
        client.prompt_tokens.return_value = (10, None)
        client.chat.return_value = (
            {"choices": [{"message": {"content": "red"}}]}, None)

        out = layers.vision_truth(
            client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertEqual(out["probes"], [])
        self.assertTrue(
            any("usage.prompt_tokens" in (f.get("body") or "") for f in out["failures"]))

    def test_success_computes_delta(self):
        client = MagicMock()
        client.prompt_tokens.return_value = (10, None)
        # 2 sizes x 1 color; always succeed
        client.chat.return_value = _ok_chat("red", 25)
        out = layers.vision_truth(
            client, colors=[((255, 0, 0), "red")], verbose=False)
        self.assertEqual(out["no_image_control_prompt_tokens"], 10)
        self.assertEqual(len(out["probes"]), 2)
        for row in out["probes"]:
            self.assertEqual(row["prompt_tokens"], 25)
            self.assertEqual(row["delta"], 15)
            self.assertEqual(row["answer"], "red")
        self.assertEqual(out["failures"], [])
        self.assertEqual(out["color_correct"], "2/2")


class TestVisionRepeatSafeParse(unittest.TestCase):
    def test_malformed_success_body_is_structured_row(self):
        client = MagicMock()
        client.chat.return_value = ({}, None)
        out = layers.vision_repeat(client, n=1, verbose=False)
        self.assertEqual(out["n"], 1)
        row = out["attempts"][0]
        self.assertIsNone(row["prompt_tokens"])
        self.assertIn("malformed", (row.get("detail") or "").lower())


if __name__ == "__main__":
    unittest.main()
