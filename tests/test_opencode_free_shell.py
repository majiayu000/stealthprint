import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from stealthprint.client import ChatClient, OPENCODE_FREE_SYSTEM


class OpenCodeFreeShellTests(unittest.TestCase):
    def setUp(self):
        self.captured = {}
        captured = self.captured

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                captured["headers"] = {k: v for k, v in self.headers.items()}
                captured["body"] = json.loads(self.rfile.read(length))
                sse = "\n".join([
                    'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"}}]}',
                    'data: {"choices":[{"index":0,"delta":{"content":"lo"}}]}',
                    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    'data: {"choices":[],"usage":{"prompt_tokens":42,"completion_tokens":2}}',
                    'data: [DONE]',
                ])
                payload = sse.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.base_url = "http://127.0.0.1:%d/v1" % self.server.server_port

    def client(self):
        return ChatClient(model="m-free", base_url=self.base_url, shell="opencode-free",
                          session_id="ses_ebd8a162affe461JZLnrZ8nEY6")

    def test_shell_requires_real_session_id(self):
        with self.assertRaises(ValueError):
            ChatClient(model="m-free", base_url=self.base_url, shell="opencode-free")
        with self.assertRaises(ValueError):
            ChatClient(model="m-free", base_url=self.base_url, shell="opencode-free",
                       session_id="857ebf31-b2bd-43fc-a613-a06c1f5e7a50")

    def test_shell_sends_free_tier_headers(self):
        self.client().chat([{"role": "user", "content": "hi"}], max_tokens=5)
        h = {k.lower(): v for k, v in self.captured["headers"].items()}
        self.assertEqual(h.get("authorization"), "Bearer public")
        self.assertTrue(h.get("user-agent", "").startswith("opencode/"))
        self.assertEqual(h.get("x-opencode-client"), "cli")
        for name in ("x-opencode-session", "x-opencode-session-id", "x-session-affinity"):
            self.assertIn(name, h)
        self.assertEqual(h.get("x-opencode-session-id"), "ses_ebd8a162affe461JZLnrZ8nEY6")

    def test_shell_wraps_body_with_system_and_stream(self):
        self.client().chat([{"role": "user", "content": "hi"}], max_tokens=5)
        b = self.captured["body"]
        self.assertEqual(b["messages"][0], {"role": "system", "content": OPENCODE_FREE_SYSTEM})
        self.assertEqual(b["messages"][1], {"role": "user", "content": "hi"})
        self.assertTrue(b["stream"])
        self.assertTrue(b["stream_options"]["include_usage"])
        self.assertEqual(b["reasoning_effort"], "none")

    def test_shell_reasoning_effort_not_overridden(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
                "reasoning_effort": "low"}
        wrapped = self.client()._wrap_body(body)
        self.assertEqual(wrapped["reasoning_effort"], "low")

    def test_shell_aggregates_sse_into_plain_response(self):
        d, err = self.client().chat([{"role": "user", "content": "hi"}], max_tokens=5)
        self.assertIsNone(err)
        self.assertEqual(d["choices"][0]["message"]["content"], "Hello")
        self.assertEqual(d["choices"][0]["finish_reason"], "stop")
        self.assertEqual(d["usage"]["prompt_tokens"], 42)

    def test_shell_prompt_tokens_reads_usage(self):
        pt, err = self.client().prompt_tokens([{"role": "user", "content": "hi"}])
        self.assertIsNone(err)
        self.assertEqual(pt, 42)

    def test_no_shell_keeps_plain_request(self):
        plain = ChatClient(model="m", base_url=self.base_url, api_key="k")
        plain.chat([{"role": "user", "content": "hi"}], max_tokens=5)
        b = self.captured["body"]
        self.assertEqual(b["messages"], [{"role": "user", "content": "hi"}])
        self.assertNotIn("stream", b)
        self.assertEqual(self.captured["headers"].get("Authorization"), "Bearer k")
        self.assertEqual(self.captured["headers"].get("User-Agent"), "stealthprint/0.1.0")


if __name__ == "__main__":
    unittest.main()
