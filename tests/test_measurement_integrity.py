"""Counterexamples from the design review; no model API calls."""
import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from stealthprint.client import ChatClient, retryable
from stealthprint import layers, probes_extra, survey
from stealthprint.cli import main


class IntegrityTests(unittest.TestCase):
    def client(self, **kwargs):
        return ChatClient('test', 'http://127.0.0.1:1', 'dummy-key', **kwargs)

    def test_contextual_tokenization_matches_real_encoding(self):
        try:
            import tiktoken
            import tokenizers
        except ImportError:
            self.skipTest('install .[tokenizer] for real BPE regression')
        enc = tiktoken.get_encoding('cl100k_base')
        base, probe = 'hel', 'lo'
        delta = len(enc.encode(base + probe)) - len(enc.encode(base))
        with tempfile.TemporaryDirectory() as directory:
            result = layers.tokenizer_differential(
                None, probes={'base': base, 'probes': [['p', probe]]},
                tokenizers_dir=directory, base_api=len(enc.encode(base)) + 10,
                api_delta={'p': delta}, verbose=False)
        self.assertEqual(result['ranking']['cl100k_base']['exact'], 1)

    def test_mixed_modes_do_not_invent_a_path(self):
        c = self.client()
        values = {'BASE': iter([10, 10, 100]), 'BASEX': iter([12, 102, 102])}
        def count(messages, **kwargs):
            return next(values[messages[0]['content']]), None
        with mock.patch.object(c, 'prompt_tokens', side_effect=count), \
             mock.patch.object(probes_extra, 'load_local_tokenizers', return_value={}):
            r = probes_extra.tokenizer_repeats(c, probes={'base': 'BASE', 'probes': [['p','X']]}, repeats=3, verbose=False)
        self.assertEqual(r['ranking'], {})
        self.assertEqual(r['api_delta'], {})
        self.assertTrue(r['count_variation'])

    def test_ambiguous_colors_are_not_correct(self):
        c = self.client()
        response = {'usage': {'prompt_tokens': 10}, 'choices': [{'message': {'content': 'red or blue'}, 'finish_reason': 'stop'}]}
        with mock.patch.object(c, 'chat', return_value=(response, None)):
            r = layers.vision_truth(c, verbose=False)
        self.assertEqual(r['color_correct'], '0/4')
        self.assertEqual(r['planned'], 4)

    def test_needle_wrong_order_is_not_exact(self):
        c = self.client()
        response = {'usage': {'prompt_tokens': 10}, 'choices': [{'message': {'content': '91024, 70553, 48219'}, 'finish_reason': 'stop'}]}
        with mock.patch.object(c, 'chat', return_value=(response, None)):
            r = layers.needle_test(c, prompt_tokens_size=100, verbose=False)
        self.assertFalse(r['exact'])
        self.assertEqual(r['recovered'], '1/3')

    def test_anthropic_total_includes_cache(self):
        c = self.client(transport='anthropic')
        with mock.patch.object(c, 'chat', return_value=({'usage': {'input_tokens': 10, 'cache_read_input_tokens': 90, 'cache_creation_input_tokens': 20}}, None)):
            self.assertEqual(c.prompt_tokens([]), (120, None))

    def test_malformed_usage_returns_error_instead_of_crashing_or_counting(self):
        c = self.client(transport='anthropic')
        for usage in ({'input_tokens': 10, 'cache_read_input_tokens': '90'},
                      {'input_tokens': True}, {'input_tokens': -1}, {'input_tokens': 1.5}):
            with self.subTest(usage=usage), mock.patch.object(c, 'request', return_value=({'usage': usage}, None)):
                count, error = c.prompt_tokens([{'role': 'user', 'content': 'hi'}])
                self.assertIsNone(count)
                self.assertEqual(error['detail']['usage'], usage)

    def test_responses_invalid_usage_is_not_a_count(self):
        c = self.client()
        for usage in (['unexpected'], {'input_tokens': '10'}, {'input_tokens': True}, {'input_tokens': -1}):
            with self.subTest(usage=usage), mock.patch.object(c, 'request', return_value=({'usage': usage}, None)):
                count, error = survey._pt_responses(c, 'test', 'hello', retries=0, delay=0)
                self.assertIsNone(count)
                self.assertEqual(error['detail']['usage'], usage)

    def test_unsupported_anthropic_probe_fails_before_request(self):
        c = self.client(transport='anthropic')
        with mock.patch.object(c, 'chat') as chat:
            with self.assertRaisesRegex(ValueError, 'OpenAI'):
                layers.vision_truth(c, verbose=False)
        chat.assert_not_called()

    def test_errors_default_does_not_generate_huge_output(self):
        c = self.client()
        with mock.patch.object(c, 'chat', return_value=(None, {'http': 400, 'body': 'invalid'})) as chat:
            layers.error_family(c, verbose=False)
        self.assertLessEqual(max(call.kwargs['max_tokens'] for call in chat.call_args_list), 16)

    def test_cli_stdout_is_json(self):
        c = self.client()
        with mock.patch('stealthprint.cli.make_client', return_value=c), \
             mock.patch.object(c, 'prompt_tokens', return_value=(10, None)), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, \
             contextlib.redirect_stderr(io.StringIO()):
            main(['--model', 'test', '--json', 'wrapper'])
        self.assertIsInstance(json.loads(stdout.getvalue()), dict)

    def test_context_failure_does_not_launch_needle(self):
        with mock.patch('stealthprint.cli.make_client'), \
             mock.patch.object(layers, 'context_search', return_value={'error': {'http': 429}}), \
             mock.patch.object(layers, 'needle_test') as needle:
            main(['--model', 'test', 'context'])
        needle.assert_not_called()

    def test_echo_counts_actual_output_and_preserves_whitespace(self):
        c = self.client()
        response = {'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                    'choices': [{'message': {'content': ' x '}, 'finish_reason': 'stop'}]}
        with mock.patch.object(c, 'chat', return_value=(response, None)), \
             mock.patch.object(probes_extra, 'load_local_tokenizers', return_value={'fake': object()}), \
             mock.patch.object(probes_extra, 'encode_len', side_effect=lambda name, tk, text: len(text)):
            result = probes_extra.echo_verify(c, probes={'probes': [['p', 'x']]}, verbose=False)
        row = result['echoes']['p']
        self.assertFalse(row['echo_exact'])
        self.assertEqual(row['content'], ' x ')
        self.assertEqual(row['offsets']['fake'], 2)


class DownloadTests(unittest.TestCase):
    def run_script(self, directory, fetch):
        script = (Path(__file__).resolve().parents[1] / 'fetch_tokenizers.sh').read_text()
        source = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with mock.patch('sys.argv', ['fetch_tokenizers', directory]), \
             mock.patch('urllib.request.urlopen', side_effect=fetch), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            exec(compile(source, 'fetch_tokenizers.sh', 'exec'), {'__name__': '__main__'})

    def test_pinned_download_and_failure_preserve_existing_candidates(self):
        urls = []
        def fetch(url, **kwargs):
            urls.append(url)
            return io.BytesIO(b'{"sha":"revision-123"}' if '/api/models/' in url else b'{"version":"1.0"}')
        with tempfile.TemporaryDirectory() as directory:
            self.run_script(directory, fetch)
            saved = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            self.assertEqual(len(json.loads(saved['sources.lock'])), 11)
            urls.clear()
            self.run_script(directory, fetch)
            self.assertTrue(all('/resolve/revision-123/' in url for url in urls))
            for error in (OSError('offline'), http.client.IncompleteRead(b'partial', 100)):
                with self.subTest(error=type(error).__name__), self.assertRaises(SystemExit) as failure:
                    self.run_script(directory, lambda *args, **kwargs: (_ for _ in ()).throw(error))
                self.assertEqual(failure.exception.code, 1)
                self.assertEqual(saved, {p.name: p.read_bytes() for p in Path(directory).iterdir()})

    def test_pinned_content_change_is_rejected(self):
        def fetch(url, **kwargs):
            return io.BytesIO(b'{"sha":"revision-123"}' if '/api/models/' in url else b'{"version":"1.0"}')
        with tempfile.TemporaryDirectory() as directory:
            self.run_script(directory, fetch)
            saved = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            with self.assertRaises(SystemExit):
                self.run_script(directory, lambda *args, **kwargs: io.BytesIO(b'{"version":"changed"}'))
            self.assertEqual(saved, {p.name: p.read_bytes() for p in Path(directory).iterdir()})


class HTTPBoundaryTests(unittest.TestCase):
    def server(self, handler):
        server = HTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def test_truncated_responses_are_recorded_as_network_failures(self):
        for status in (200, 502):
            with self.subTest(status=status):
                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        self.send_response(status)
                        self.send_header('Content-Length', '100')
                        self.end_headers()
                        self.wfile.write(b'{"partial":')
                    def log_message(self, *args): pass
                server = self.server(Handler)
                with tempfile.TemporaryDirectory() as directory:
                    record = Path(directory) / 'requests.jsonl'
                    c = ChatClient('m', f'http://127.0.0.1:{server.server_port}', 'dummy', record_path=record)
                    result, error = c.request('POST', '/chat/completions', {})
                    self.assertIsNone(result)
                    self.assertEqual(error['http'], status)
                    self.assertIn('IncompleteRead', error['body'])
                    self.assertTrue(retryable(error))
                    row = json.loads(record.read_text())
                    self.assertEqual(row['raw_response'], '{"partial":')
                    self.assertEqual(row['error'], error)

    def test_redirect_cannot_forward_credentials(self):
        received = []
        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')
            def log_message(self, *args): pass
        target = self.server(Target)
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{target.server_port}/destination')
                self.end_headers()
            def log_message(self, *args): pass
        source = self.server(Redirect)
        c = ChatClient('m', f'http://127.0.0.1:{source.server_port}', 'dummy-only')
        _, error = c.list_models()
        self.assertEqual(received, [])
        self.assertEqual(error['http'], 302)

    def test_embedded_error_and_raw_record_are_preserved(self):
        body = {'error': {'code': 502, 'message': 'provider_unavailable'}}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            def log_message(self, *args): pass
        server = self.server(Handler)
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / 'requests.jsonl'
            c = ChatClient('m', f'http://127.0.0.1:{server.server_port}', 'dummy-secret', record_path=record)
            _, error = c.prompt_tokens([{'role':'user','content':'hello'}])
            self.assertEqual(error['http'], 200)
            self.assertEqual(error['detail'], body['error'])
            saved = record.read_text()
            self.assertNotIn('dummy-secret', saved)
            row = json.loads(saved)
            self.assertEqual(row['response'], body)
            self.assertEqual(row['request']['messages'][0]['content'], 'hello')


if __name__ == '__main__':
    unittest.main()
