"""scripts/opencode_relay.mjs under bun with a stubbed upstream; loopback only, no opencode.ai call."""
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

from stealthprint.client import ChatClient

RELAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "opencode_relay.mjs")
BUN = shutil.which("bun")
CHAT = json.dumps({"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": 4000})

# Preloaded into the relay: its upstream fetch lands in a file instead of on opencode.ai.
STUB = """
import fs from 'fs';
globalThis.fetch = async (url, init = {}) => {
  fs.appendFileSync(process.env.RELAY_TEST_UPSTREAM, JSON.stringify({ url: String(url),
    method: init.method || 'GET', authorization: new Headers(init.headers).get('authorization') }) + '\\n');
  return new Response(JSON.stringify({ object: 'list', data: [{ id: 'm' }], usage: { prompt_tokens: 7 },
    choices: [{ message: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }] }),
    { status: 200, headers: { 'content-type': 'application/json' } });
};
"""


@unittest.skipUnless(BUN, "bun is not installed")
class RelayAdmissionTests(unittest.TestCase):
    """Only a local program may spend the key the relay holds; a web page must not."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.dir.cleanup)
        directory = cls.dir.name
        cls.upstream = os.path.join(directory, "upstream.jsonl")
        for name, text in (("auth.json", json.dumps({"opencode-go": {"key": "relay-test-key"}})), ("stub.mjs", STUB)):
            with open(os.path.join(directory, name), "w", encoding="utf-8") as stream:
                stream.write(text)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        # The proxy points nowhere: should the stub ever not load, nothing leaves this machine.
        env = {"HOME": directory, "TMPDIR": directory, "OPENCODE_AUTH": os.path.join(directory, "auth.json"),
               "RELAY_TEST_UPSTREAM": cls.upstream, "HTTPS_PROXY": "http://127.0.0.1:9"}
        cls.relay = subprocess.Popen(
            [BUN, "--preload", os.path.join(directory, "stub.mjs"), RELAY, "go", str(cls.port)], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        cls.addClassCleanup(cls.stop)
        watchdog = threading.Timer(30, cls.relay.kill)
        watchdog.start()
        started = cls.relay.stderr.readline()
        watchdog.cancel()
        if "opencode relay (go)" not in started:
            raise RuntimeError("relay did not start: %r" % started)

    @classmethod
    def stop(cls):
        cls.relay.terminate()
        cls.relay.wait(timeout=10)
        cls.relay.stderr.close()

    def setUp(self):
        if os.path.exists(self.upstream):
            os.remove(self.upstream)

    def upstream_calls(self):
        if not os.path.exists(self.upstream):
            return []
        with open(self.upstream, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]

    def ask(self, headers, method="POST", path="/v1/chat/completions"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        self.addCleanup(connection.close)
        connection.request(method, path, body=CHAT if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status

    def test_stealthprint_requests_reach_upstream_with_the_key(self):
        client = ChatClient("m", "http://127.0.0.1:%d/v1" % self.port, "x")
        self.assertEqual(client.prompt_tokens([{"role": "user", "content": "x"}]), (7, None))
        self.assertEqual(client.list_models(), (["m"], None))
        self.assertEqual(self.ask({"Content-Type": "application/json", "Host": "localhost:%d" % self.port}), 200)
        self.assertEqual([(c["method"], c["url"], c["authorization"]) for c in self.upstream_calls()], [
            ("POST", "https://opencode.ai/zen/go/v1/chat/completions", "Bearer relay-test-key"),
            ("GET", "https://opencode.ai/zen/go/v1/models", "Bearer relay-test-key"),
            ("POST", "https://opencode.ai/zen/go/v1/chat/completions", "Bearer relay-test-key")])

    def test_a_web_page_cannot_spend_the_key(self):
        json_type = {"Content-Type": "application/json"}
        simple = {"Content-Type": "text/plain;charset=UTF-8"}   # what a cross-site POST can send without a preflight
        rebound = {"Host": "rebind.evil.example:%d" % self.port}
        cases = [
            ("cross-site simple POST", "POST", dict(simple, Origin="https://evil.example"), 403),
            ("cross-site POST after a preflight", "POST", dict(json_type, Origin="https://evil.example"), 403),
            ("browser request without Origin", "POST", {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, 403),
            ("simple content type alone", "POST", simple, 415),
            ("DNS rebinding POST", "POST", dict(json_type, **rebound), 403),
            ("DNS rebinding GET", "GET", rebound, 403),
            ("cross-site GET", "GET", {"Sec-Fetch-Site": "cross-site"}, 403),
        ]
        for name, method, headers, status in cases:
            with self.subTest(name):
                path = "/v1/chat/completions" if method == "POST" else "/v1/models"
                self.assertEqual(self.ask(headers, method=method, path=path), status)
                self.assertEqual(self.upstream_calls(), [])


if __name__ == "__main__":
    unittest.main()
