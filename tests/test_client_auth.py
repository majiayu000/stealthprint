"""Unit tests for ChatClient API key resolution (SEC-07)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from stealthprint.client import ChatClient


class TestChatClientAuth(unittest.TestCase):
    def setUp(self):
        # Isolate from the developer's real env / auth.json.
        self._env_patch = mock.patch.dict(os.environ, {}, clear=True)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _write_auth_json(self, key="sk-from-opencode"):
        path = os.path.join(self._tmpdir, "auth.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"opencode-go": {"key": key}}, f)
        return path

    def test_explicit_api_key_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmpdir = tmp
            auth_path = self._write_auth_json()
            os.environ["STEALTHPRINT_API_KEY"] = "sk-from-env"
            os.environ["STEALTHPRINT_USE_OPENCODE_AUTH"] = "1"
            with mock.patch.object(
                ChatClient, "_opencode_fallback_key",
                side_effect=AssertionError("auth.json must not be consulted"),
            ):
                # Still should not call fallback when constructor key present.
                client = ChatClient(
                    model="m",
                    base_url="https://example.invalid/v1",
                    api_key="sk-ctor",
                )
            self.assertEqual(client.api_key, "sk-ctor")
            self.assertEqual(client._api_key_source, "constructor")
            self.assertTrue(os.path.exists(auth_path))

    def test_env_key_used_when_no_constructor_key(self):
        os.environ["STEALTHPRINT_API_KEY"] = "sk-from-env"
        client = ChatClient(model="m", base_url="https://example.invalid/v1")
        self.assertEqual(client.api_key, "sk-from-env")
        self.assertEqual(client._api_key_source, "STEALTHPRINT_API_KEY")

    def test_missing_key_raises_without_reading_auth_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tmpdir = tmp
            auth_path = self._write_auth_json("sk-should-not-load")
            home = tmp  # expanduser("~") redirected via HOME
            os.environ["HOME"] = home
            # Put auth where the real path would resolve under this HOME.
            opencode_dir = os.path.join(home, ".local", "share", "opencode")
            os.makedirs(opencode_dir, exist_ok=True)
            os.replace(auth_path, os.path.join(opencode_dir, "auth.json"))

            with self.assertRaises(ValueError) as ctx:
                ChatClient(model="m", base_url="https://example.invalid/v1")
            msg = str(ctx.exception)
            self.assertIn("STEALTHPRINT_API_KEY", msg)
            self.assertIn("STEALTHPRINT_USE_OPENCODE_AUTH", msg)

    def test_opencode_fallback_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = tmp
            os.environ["HOME"] = home
            opencode_dir = os.path.join(home, ".local", "share", "opencode")
            os.makedirs(opencode_dir)
            with open(os.path.join(opencode_dir, "auth.json"), "w", encoding="utf-8") as f:
                json.dump({"opencode": {"key": "sk-opencode"}}, f)

            with self.assertRaises(ValueError):
                ChatClient(model="m", base_url="https://example.invalid/v1")

            os.environ["STEALTHPRINT_USE_OPENCODE_AUTH"] = "1"
            client = ChatClient(model="m", base_url="https://example.invalid/v1")
            self.assertEqual(client.api_key, "sk-opencode")
            self.assertEqual(client._api_key_source, "opencode_auth.json")


if __name__ == "__main__":
    unittest.main()
