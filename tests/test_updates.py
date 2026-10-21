"""Exercise source failures and history transitions without model/API calls."""
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from http.client import IncompleteRead
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from stealthprint.updates import SOURCES, group_updates, load_state, refresh_monitor


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
ROUTER = next(s for s in SOURCES if s['id'] == 'openrouter')
FEED = SOURCES[0]
HF = next(s for s in SOURCES if s['kind'] == 'huggingface')
SDK = next(s for s in SOURCES if s['id'] == 'openai-sdk')


def response(payload, headers=None):
    return 200, headers or {}, json.dumps(payload).encode()


def catalog(*names):
    return {'data': [{'id': name, 'name': name, 'context_length': 128000} for name in names],
            'total_count': len(names), 'links': {'next': None}}


def feed(identity, text='a report', kind='leak'):
    return {'items': [{'id': identity, 'title': 'Example model', 'url': 'https://example.com/model',
                      'date_published': '2026-10-17T01:00:00Z', 'content_text': text, 'tags': [kind]}]}


def hf_model(name):
    return {'id': name, 'sha': 'revision1', 'siblings': [{'rfilename': 'config.json'}]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_refresh(self, *replies, sources=(ROUTER,), minutes=0):
        with patch('stealthprint.updates._fetch', side_effect=replies) as fetch:
            state = refresh_monitor(self.directory, sources=sources, now=NOW + timedelta(minutes=minutes))
        return state, fetch

    def test_directory_baseline_reorder_repeat_and_conditional_fetch(self):
        state, _ = self.run_refresh(response(catalog('a', 'b'), {'etag': 'v1'}))
        self.assertEqual(state['events'], [])
        evidence = state['sources']['openrouter']['evidence']
        state, _ = self.run_refresh(response(catalog('b', 'a'), {'etag': 'v2'}), minutes=5)
        self.assertEqual(state['events'], [])
        self.assertEqual(state['sources']['openrouter']['evidence'], evidence)
        state, fetch = self.run_refresh((304, {}, b''), minutes=10)
        self.assertEqual(fetch.call_args.args[1], {'If-None-Match': 'v2'})
        self.assertEqual(state['sources']['openrouter']['last_success_at'], (NOW + timedelta(minutes=10)).isoformat())
        self.assertIsNone(state['sources']['openrouter']['error'])
        self.assertEqual(len(list(self.directory.glob('evidence/*'))), 1)

    def test_missing_and_restored_have_immutable_before_and_after_evidence(self):
        self.run_refresh(response(catalog('a', 'b')))
        removed, _ = self.run_refresh(response(catalog('a')), minutes=5)
        event = removed['events'][0]
        self.assertEqual((event['entity_id'], event['kind']), ('b', 'missing'))
        self.assertIsNone(event['after'])
        self.assertIn('b', gzip.decompress((self.directory / event['before_evidence']).read_bytes()).decode())
        restored, _ = self.run_refresh(response(catalog('a', 'b')), minutes=10)
        self.assertEqual([e['kind'] for e in restored['events']], ['missing', 'restored'])
        self.assertNotEqual(restored['events'][0]['id'], restored['events'][1]['id'])

    def test_feed_rolloff_does_not_retract_and_revision_preserves_actual_old_evidence(self):
        first, _ = self.run_refresh(response(feed('old', 'original')), sources=(FEED,))
        original_evidence = first['events'][0]['evidence']
        self.run_refresh(response(feed('other')), sources=(FEED,), minutes=5)
        revised, _ = self.run_refresh(response(feed('old', 'corrected', 'release')), sources=(FEED,), minutes=10)
        self.assertEqual([e['kind'] for e in revised['events']], ['reported', 'reported', 'changed'])
        event = revised['events'][-1]
        self.assertEqual(event['before']['summary'], 'original')
        self.assertEqual(event['after']['summary'], 'corrected')
        self.assertEqual(event['before_evidence'], original_evidence)
        self.assertEqual(event['published_at'], '2026-10-17T01:00:00Z')
        self.assertEqual(len(revised['sources'][FEED['id']]['inventory']), 2)
        repeated, _ = self.run_refresh(response(feed('old', 'corrected', 'release')), sources=(FEED,), minutes=15)
        self.assertEqual(repeated['events'], revised['events'])

    def test_one_source_failure_does_not_block_other_and_keeps_baseline(self):
        self.run_refresh(response(catalog('a')))
        state, _ = self.run_refresh(OSError('offline'), response(feed('news')), sources=(ROUTER, FEED), minutes=5)
        self.assertEqual(list(state['sources']['openrouter']['inventory']), ['a'])
        self.assertIn('offline', state['sources']['openrouter']['error'])
        self.assertEqual(state['sources']['openrouter']['last_success_at'], NOW.isoformat())
        self.assertEqual(state['events'][0]['source_id'], FEED['id'])
        state, _ = self.run_refresh(response(catalog('a')), sources=(ROUTER,), minutes=10)
        self.assertIsNone(state['sources']['openrouter']['error'])

    def test_truncated_http_body_does_not_stop_another_source(self):
        state, _ = self.run_refresh(IncompleteRead(b'partial', 10), response(feed('news')), sources=(ROUTER, FEED))
        self.assertIn('IncompleteRead', state['sources']['openrouter']['error'])
        self.assertIsNone(state['sources'][FEED['id']]['error'])
        self.assertEqual(len(state['events']), 1)

    def test_rate_limit_uses_retry_after_without_retrying_early(self):
        error = HTTPError(ROUTER['url'], 429, 'slow down', {'Retry-After': '3600'}, io.BytesIO())
        state, _ = self.run_refresh(error)
        self.assertEqual(state['sources']['openrouter']['next_check_at'], (NOW + timedelta(hours=1)).isoformat())
        state, fetch = self.run_refresh(minutes=30)
        fetch.assert_not_called()
        self.assertIn('429', state['sources']['openrouter']['error'])

    def test_retry_after_http_date_and_bad_value(self):
        for retry, expected in [('Tue, 20 Oct 2026 01:00:00 GMT', 60), ('garbage', 5)]:
            with self.subTest(retry=retry):
                error = HTTPError(ROUTER['url'], 503, 'busy', {'Retry-After': retry}, io.BytesIO())
                with patch('stealthprint.updates._fetch', side_effect=error):
                    state = refresh_monitor(self.directory / retry[:3], sources=(ROUTER,), now=NOW)
                self.assertEqual(state['sources']['openrouter']['next_check_at'], (NOW + timedelta(minutes=expected)).isoformat())

    def test_corrupt_or_incomplete_response_cannot_replace_baseline(self):
        self.run_refresh(response(catalog('a')))
        for payload in [{'data': [], 'total_count': 1},
                        {'data': [], 'links': {'next': 'https://example.com/next'}},
                        catalog('a', 'a'), {'data': None}]:
            with self.subTest(payload=payload):
                state, _ = self.run_refresh(response(payload), minutes=5)
                self.assertEqual(list(state['sources']['openrouter']['inventory']), ['a'])
                self.assertEqual(state['events'], [])
                self.assertIsNotNone(state['sources']['openrouter']['error'])
                # Make the following independent failure due at the same timestamp.
                state['sources']['openrouter']['next_check_at'] = NOW.isoformat()
                (self.directory / 'state.json').write_text(json.dumps(state))

    def test_malformed_source_fields_are_isolated_at_the_parser_boundary(self):
        invalid_date = feed('bad-date')
        invalid_date['items'][0]['date_published'] = None
        invalid_url = feed('bad-url')
        invalid_url['items'][0]['url'] = 42
        models_dev = next(s for s in SOURCES if s['id'] == 'models-dev')
        cases = [(FEED, invalid_date), (FEED, invalid_url), (ROUTER, []),
                 (ROUTER, {'data': [], 'links': []}), (models_dev, {'opencode': {'models': None}})]
        for index, (source, payload) in enumerate(cases):
            with self.subTest(source=source['id'], payload=payload):
                other = ROUTER if source == FEED else FEED
                good = catalog('ok') if other == ROUTER else feed('ok')
                with patch('stealthprint.updates._fetch', side_effect=[response(payload), response(good)]):
                    state = refresh_monitor(self.directory / str(index), sources=(source, other), now=NOW)
                self.assertIsNotNone(state['sources'][source['id']]['error'])
                self.assertIsNone(state['sources'][other['id']]['error'])

    def test_304_without_baseline_is_not_success(self):
        state, _ = self.run_refresh((304, {}, b''))
        self.assertNotIn('last_success_at', state['sources']['openrouter'])
        self.assertIn('304', state['sources']['openrouter']['error'])

    def test_huggingface_follows_all_pages_and_never_partially_commits(self):
        next_url = HF['url'] + '&cursor=next'
        first = response([hf_model('deepseek-ai/a')], {'link': '<' + next_url + '>; rel="next"'})
        second = response([hf_model('deepseek-ai/b')])
        state, fetch = self.run_refresh(first, second, sources=(HF,))
        self.assertEqual(fetch.call_count, 2)
        before = state['sources'][HF['id']]['inventory']
        self.assertEqual(len(before), 2)
        failed, _ = self.run_refresh(first, OSError('page two failed'), sources=(HF,), minutes=15)
        self.assertEqual(failed['sources'][HF['id']]['inventory'], before)
        self.assertEqual(failed['events'], [])

    def test_pagination_cannot_leave_the_source_or_loop(self):
        for url in ['https://other.example/page', HF['url']]:
            with self.subTest(url=url):
                first = response([hf_model('deepseek-ai/a')], {'link': '<' + url + '>; rel="next"'})
                with patch('stealthprint.updates._fetch', return_value=first) as fetch:
                    state = refresh_monitor(self.directory / hashlib.sha256(url.encode()).hexdigest(), sources=(HF,), now=NOW)
                self.assertEqual(fetch.call_count, 1)
                self.assertNotIn('inventory', state['sources'][HF['id']])

    def test_sdk_is_parsed_without_execution_and_ignores_formatting(self):
        def sdk(text):
            return response({'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode(),
                             'name': 'chat_model.py', 'html_url': 'https://github.com/example/model.py'})
        initial = 'from typing import Literal\nModel = Literal["model-a"]\nraise RuntimeError("must not execute")\n'
        first, _ = self.run_refresh(sdk(initial), sources=(SDK,))
        self.assertEqual(first['sources'][SDK['id']]['inventory']['model-types']['models'], ['model-a'])
        same, _ = self.run_refresh(sdk(initial.replace(' = ', '=')), sources=(SDK,), minutes=15)
        self.assertEqual(same['events'], [])
        changed, _ = self.run_refresh(sdk(initial + '\n"""model-a is no longer supported"""\n'), sources=(SDK,), minutes=30)
        self.assertEqual(changed['events'][0]['kind'], 'changed')
        self.assertIn('no longer', changed['events'][0]['after']['notes'][0])

    def test_raw_evidence_round_trips_and_filename_hash_matches(self):
        state, _ = self.run_refresh(response(catalog('model/a'), {'date': 'today', 'etag': 'v1'}))
        relative = state['sources']['openrouter']['evidence']
        body = gzip.decompress((self.directory / relative).read_bytes())
        self.assertEqual(hashlib.sha256(body).hexdigest(), Path(relative).name.split('.')[0])
        saved = json.loads(body)['responses'][0]
        self.assertEqual(saved['url'], ROUTER['url'])
        self.assertEqual(json.loads(saved['body']), catalog('model/a'))
        self.assertEqual(saved['headers']['etag'], 'v1')

    def test_invalid_source_link_does_not_enter_published_data(self):
        bad = feed('unsafe')
        bad['items'][0]['url'] = 'javascript:alert(1)'
        state, _ = self.run_refresh(response(bad), sources=(FEED,))
        self.assertEqual(state['events'], [])
        self.assertIn('HTTP(S)', state['sources'][FEED['id']]['error'])

    def test_history_grouping_keeps_release_and_leak(self):
        old, new = feed('leak')['items'][0], feed('release', kind='release')['items'][0]
        new['date_published'] = '2026-10-17T08:00:00Z'
        snapshot = {'catalog': {'pages': []}, 'feed': {'items': [old, new]}}
        self.assertEqual(group_updates(snapshot)[0]['events'], [new, old])
        self.assertEqual(snapshot['feed']['items'], [old, new])


if __name__ == '__main__':
    unittest.main()
