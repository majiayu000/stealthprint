import contextlib
import io
import unittest
from unittest import mock

from stealthprint import layers
from stealthprint.client import ChatClient


def response(prompt_tokens):
    return {'usage': {'prompt_tokens': prompt_tokens}}, None


class ContextSearchTests(unittest.TestCase):
    def setUp(self):
        self.client = ChatClient('test', 'https://example.invalid/v1', 'dummy-test-key')

    def test_failed_initial_probe_preserves_error_and_no_success(self):
        for status in (413, 503, 429, None):
            error = {'http': status, 'body': 'failed input'}
            with self.subTest(status=status), mock.patch.object(self.client, 'chat', return_value=(None, error)) as chat:
                r = layers.context_search(self.client, verbose=False)
            self.assertIsNone(r['max_verified_chars'])
            self.assertEqual(r['history'][0]['error'], error)
            self.assertEqual(r['error'], error)
            self.assertEqual(len(chat.call_args.args[0][0]['content']), 10000)
            chat.assert_called_once()

    def test_transient_failure_keeps_lower_bound_and_stops(self):
        error = {'http': 429, 'body': 'rate limited'}
        with mock.patch.object(self.client, 'chat', side_effect=[response(2500), (None, error)]) as chat:
            r = layers.context_search(self.client, max_chars=100000, verbose=False)
        self.assertEqual(r['max_verified_chars'], 10000)
        self.assertEqual(r['max_verified_prompt_tokens'], 2500)
        self.assertEqual(r['error'], error)
        self.assertEqual(len(r['history']), 2)
        self.assertEqual(chat.call_count, 2)

    def test_requested_char_cap_includes_task(self):
        with mock.patch.object(self.client, 'chat', return_value=response(0)) as chat:
            r = layers.context_search(self.client, max_chars=100, verbose=False)
        self.assertEqual(len(chat.call_args.args[0][0]['content']), 100)
        self.assertEqual(r['max_verified_chars'], 100)
        self.assertEqual(r['max_verified_prompt_tokens'], 0)
        chat.assert_called_once()

    def test_ladder_reaches_cap_without_exceeding_it(self):
        with mock.patch.object(self.client, 'chat', return_value=response(10)) as chat:
            r = layers.context_search(self.client, max_chars=95000, min_step=10000, verbose=False)
        sizes = [len(c.args[0][0]['content']) for c in chat.call_args_list]
        self.assertEqual(sizes, [10000, 20000, 40000, 80000, 95000])
        self.assertEqual(r['max_verified_chars'], 95000)
        self.assertNotIn('error', r)

    def test_too_small_cap_fails_before_network(self):
        with mock.patch.object(self.client, 'chat') as chat:
            with self.assertRaises(ValueError):
                layers.context_search(self.client, max_chars=1, verbose=False)
        chat.assert_not_called()

    def test_initial_failure_prints_no_success(self):
        with mock.patch.object(self.client, 'chat', return_value=(None, {'http':503,'body':'unavailable'})), contextlib.redirect_stdout(io.StringIO()) as output:
            layers.context_search(self.client)
        self.assertNotIn('max verified:', output.getvalue())
        self.assertIn('unavailable', output.getvalue())


if __name__ == '__main__':
    unittest.main()
