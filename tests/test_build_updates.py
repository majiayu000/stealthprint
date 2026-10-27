"""Published package inspection without installation or executing JavaScript."""
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from stealthprint.updates import (GEMINI_MODELS_PATH, GEMINI_PACKAGE, SOURCES,
                                 _fetch, candidate_ids, load_state, refresh_monitor)


SOURCE = next(source for source in SOURCES if source['kind'] == 'gemini-build')
ROUTER = next(source for source in SOURCES if source['id'] == 'openrouter')
NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
FIRST = "export const DEFAULT_GEMINI_MODEL = 'gemini-example-pro';\n"
SECOND = FIRST + "export let NEXT_GEMINI_MODEL = 'gemini-example-flash';\n"


def package(text=FIRST, version='1.0.0', members=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, value, kind in members or [(GEMINI_MODELS_PATH, text, tarfile.REGTYPE)]:
            body = value.encode()
            member = tarfile.TarInfo(name)
            member.type, member.size = kind, len(body)
            archive.addfile(member, io.BytesIO(body))
    body = output.getvalue()
    metadata = {'name': GEMINI_PACKAGE, 'version': version, 'dist': {
        'tarball': 'https://registry.npmjs.org/' + GEMINI_PACKAGE + '/-/gemini-cli-core-' + version + '.tgz',
        'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(body).digest()).decode(),
    }}
    return metadata, body


def reply(payload):
    return 200, {'etag': 'metadata-etag'}, json.dumps(payload).encode()


class BuildUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def refresh(self, *replies, hour=0, sources=(SOURCE,)):
        with patch('stealthprint.updates._fetch', side_effect=replies) as fetch:
            state = refresh_monitor(self.directory, sources=sources, now=NOW + timedelta(hours=hour))
        return state, fetch

    def test_baseline_verified_evidence_and_no_redownload_for_same_package(self):
        metadata, body = package()
        state, _ = self.refresh(reply(metadata), (200, {}, body))
        self.assertEqual(state['events'], [])
        saved = state['sources'][SOURCE['id']]
        envelope = json.loads(gzip.decompress((self.directory / saved['evidence']).read_bytes()))
        self.assertEqual(envelope['package']['version'], '1.0.0')
        file = envelope['artifact']['files'][0]
        self.assertEqual(file['body'], FIRST)
        self.assertEqual(file['sha256'], hashlib.sha256(FIRST.encode()).hexdigest())
        self.assertTrue(envelope['artifact']['integrity_verified'])
        same, fetch = self.refresh(reply(metadata), hour=1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(same['events'], [])
        unchanged, fetch = self.refresh((304, {}, b''), hour=2)
        self.assertEqual(fetch.call_args.args[1], {'If-None-Match': 'metadata-etag'})
        self.assertEqual(unchanged['sources'][SOURCE['id']]['last_success_at'], (NOW + timedelta(hours=2)).isoformat())

    def test_version_and_formatting_changes_are_not_model_changes(self):
        metadata, body = package(SECOND)
        initial, _ = self.refresh(reply(metadata), (200, {}, body))
        metadata, body = package(SECOND.replace(' = ', '=').replace('\n', ' '), version='1.0.1')
        updated, _ = self.refresh(reply(metadata), (200, {}, body), hour=1)
        self.assertEqual(updated['events'], [])
        source = updated['sources'][SOURCE['id']]
        self.assertEqual(source['artifact']['version'], '1.0.1')
        self.assertEqual(source['evidence'], initial['sources'][SOURCE['id']]['evidence'])

    def test_added_removed_restored_preserve_files_and_candidates(self):
        metadata, body = package()
        self.refresh(reply(metadata), (200, {}, body))
        metadata, body = package(SECOND, version='1.1.0')
        changed, _ = self.refresh(reply(metadata), (200, {}, body), hour=1)
        event = changed['events'][0]
        self.assertEqual(candidate_ids(event, 'gemini-build', changed['events']), ['gemini-example-flash'])
        self.assertEqual(event['after']['symbols'], ['NEXT_GEMINI_MODEL'])
        before = json.loads(gzip.decompress((self.directory / event['before_evidence']).read_bytes()))
        after = json.loads(gzip.decompress((self.directory / event['evidence']).read_bytes()))
        self.assertEqual((before['package']['version'], after['package']['version']), ('1.0.0', '1.1.0'))
        self.assertEqual(before['artifact']['files'][0]['body'], FIRST)
        self.assertEqual(after['artifact']['files'][0]['body'], SECOND)
        metadata, body = package(FIRST, version='1.2.0')
        self.refresh(reply(metadata), (200, {}, body), hour=2)
        metadata, body = package(SECOND, version='1.3.0')
        restored, _ = self.refresh(reply(metadata), (200, {}, body), hour=3)
        self.assertEqual([e['kind'] for e in restored['events']], ['added', 'missing', 'restored'])
        self.assertEqual(candidate_ids(restored['events'][-1], 'gemini-build', restored['events']), [])

    def test_integrity_or_archive_failures_keep_baseline_and_other_source_running(self):
        metadata, body = package()
        baseline, _ = self.refresh(reply(metadata), (200, {}, body))
        bad_url, _ = package(SECOND)
        bad_url['dist']['tarball'] = 'https://elsewhere.example/package.tgz'
        missing, missing_body = package(members=[('package/installer.js', FIRST, tarfile.REGTYPE)])
        duplicate, duplicate_body = package(members=[(GEMINI_MODELS_PATH, FIRST, tarfile.REGTYPE)] * 2)
        link, link_body = package(members=[(GEMINI_MODELS_PATH, '', tarfile.SYMTYPE)])
        oversized, oversized_body = package('x' * (2 * 1024 * 1024 + 1))
        broken, _ = package(SECOND)
        broken['dist']['integrity'] = 'sha512-' + base64.b64encode(hashlib.sha512(b'not a tar').digest()).decode()
        cases = [(reply(bad_url),), (reply(package(SECOND)[0]), (200, {}, b'wrong bytes')),
                 (reply(missing), (200, {}, missing_body)), (reply(duplicate), (200, {}, duplicate_body)),
                 (reply(link), (200, {}, link_body)), (reply(oversized), (200, {}, oversized_body)),
                 (reply(broken), (200, {}, b'not a tar'))]
        good = reply({'data': [{'id': 'healthy', 'name': 'healthy'}]})
        for hour, responses in enumerate(cases, 1):
            with self.subTest(hour=hour):
                state, _ = self.refresh(*responses, good, hour=hour, sources=(SOURCE, ROUTER))
                saved = state['sources'][SOURCE['id']]
                self.assertIsNotNone(saved['error'])
                self.assertEqual(saved['inventory'], baseline['sources'][SOURCE['id']]['inventory'])
                self.assertEqual(saved['evidence'], baseline['sources'][SOURCE['id']]['evidence'])
                self.assertIsNone(state['sources'][ROUTER['id']]['error'])
                self.assertFalse(any(e['source_id'] == SOURCE['id'] for e in state['events']))

    def test_only_exported_model_literals_are_observed_and_never_executed(self):
        text = FIRST + '''
// export const COMMENT_MODEL = 'gemini-comment';
/* export const COMMENT_MODEL = 'gemini-block-comment'; */
export const GEMINI_MODEL_ALIAS_PRO = 'pro';
export const PREVIEW_GEMINI_MODEL_AUTO = 'auto-gemini-example';
export const ALIAS_MODEL = DEFAULT_GEMINI_MODEL;
const EXAMPLE_MODEL = 'gemini-test';
throw new Error('This must never execute');
'''
        metadata, body = package(text)
        state, _ = self.refresh(reply(metadata), (200, {}, body))
        self.assertEqual(list(state['sources'][SOURCE['id']]['inventory']), ['gemini-example-pro'])
        metadata, body = package(FIRST + "export const NEXT_MODEL = 'gemini-' + suffix;\n", version='2.0.0')
        failed, _ = self.refresh(reply(metadata), (200, {}, body), hour=1)
        self.assertIn('simple literal', failed['sources'][SOURCE['id']]['error'])
        self.assertEqual(failed['events'], [])

    def test_streamed_download_truncation(self):
        stream = io.BytesIO(b'hello')
        stream.code, stream.headers = 200, {'Content-Length': '12'}
        with patch('stealthprint.updates.urllib.request.urlopen', return_value=stream):
            with self.assertRaisesRegex(ValueError, 'truncated'):
                _fetch('https://example.com/file', {}, float('inf'))

    def local_server(self, handle):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                handle(self)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port)

    def test_sdk_readded_literals_are_not_new_candidates(self):
        sdk = next(source for source in SOURCES if source['id'] == 'openai-sdk')

        def snapshot(*models):
            text = 'from typing import Literal\nModel = Literal[' + repr(models)[1:-1] + ']'
            return reply({'name': 'model.py', 'html_url': 'https://example.com/model.py',
                          'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()})

        self.refresh(snapshot('baseline', 'removed'), sources=(sdk,))
        added, _ = self.refresh(snapshot('baseline', 'removed', 'first-new'), hour=1, sources=(sdk,))
        self.assertEqual(candidate_ids(added['events'][-1], 'sdk', added['events']), ['first-new'])
        self.refresh(snapshot('baseline'), hour=2, sources=(sdk,))
        # Restore a baseline literal and a later discovery alongside a genuinely new one.
        restored, _ = self.refresh(snapshot('baseline', 'removed', 'first-new', 'second-new'),
                                   hour=3, sources=(sdk,))
        event = restored['events'][-1]
        self.assertEqual(candidate_ids(event, 'sdk', restored['events']), ['second-new'])
        # A later restoration must not rewrite the earlier first-observation badge.
        self.assertEqual(candidate_ids(added['events'][-1], 'sdk', restored['events']), ['first-new'])
        self.assertEqual(event['before']['models'], ['baseline'])
        self.assertEqual(event['after']['models'], ['baseline', 'first-new', 'removed', 'second-new'])
        self.refresh(snapshot('baseline'), hour=4, sources=(sdk,))
        restored, _ = self.refresh(snapshot('baseline', 'removed', 'first-new', 'second-new'),
                                   hour=5, sources=(sdk,))
        self.assertEqual(candidate_ids(restored['events'][-1], 'sdk', restored['events']), [])

    def test_download_checks_deadline_after_blocking_body_and_eof_reads(self):
        for eof in (False, True):
            with self.subTest(eof=eof):
                clock = [0]
                reading = threading.Event()

                def handle(request):
                    request.send_response(200)
                    request.end_headers()
                    reading.wait(5)
                    clock[0] = 91
                    if not eof:
                        request.wfile.write(b'late body')
                        request.wfile.flush()

                url = self.local_server(handle)
                original_read = HTTPResponse.read1

                def read(response, size):
                    reading.set()
                    return original_read(response, size)

                with patch('stealthprint.updates.time.monotonic', side_effect=lambda: clock[0]), \
                        patch('http.client.HTTPResponse.read1', read):
                    with self.assertRaisesRegex(TimeoutError, '90 seconds'):
                        _fetch(url, {}, float('inf'))

    def test_serial_sources_share_runtime_budget_and_persist_success(self):
        clock, requests = [0], []
        body = json.dumps({'data': [{'id': 'kept', 'name': 'kept'}]}).encode()

        def handle(request):
            requests.append(request.path)
            # Each source takes less than its own 90-second download limit,
            # but four together consume the 240-second collection budget.
            clock[0] += 70
            request.send_response(200)
            request.send_header('Content-Length', str(len(body)))
            request.end_headers()
            request.wfile.write(body)
            request.wfile.flush()

        url = self.local_server(handle)
        sources = tuple({**ROUTER, 'id': 'source-' + str(index), 'url': url + '/' + str(index)}
                        for index in range(6))
        with patch('stealthprint.updates.time.monotonic', side_effect=lambda: clock[0]):
            state = refresh_monitor(self.directory, sources=sources, now=NOW)
        self.assertEqual(requests, ['/0', '/1', '/2', '/3'])
        self.assertEqual(load_state(self.directory), state)
        for source in sources[:3]:
            saved = state['sources'][source['id']]
            self.assertIsNone(saved['error'])
            self.assertEqual(list(saved['inventory']), ['kept'])
            self.assertTrue((self.directory / saved['evidence']).is_file())
        for source in sources[3:]:
            saved = state['sources'][source['id']]
            self.assertIn('TimeoutError', saved['error'])
            self.assertNotIn('inventory', saved)

    def test_paginated_source_uses_the_same_collection_deadline(self):
        clock, requests = [0], []

        def handle(request):
            requests.append(request.path)
            clock[0] += 70
            page = len(requests)
            body = json.dumps([{'id': 'example/model-' + str(page), 'sha': 'revision',
                                'siblings': []}]).encode()
            request.send_response(200)
            request.send_header('Link', '<' + url + '/models?page=' + str(page + 1) + '>; rel="next"')
            request.send_header('Content-Length', str(len(body)))
            request.end_headers()
            request.wfile.write(body)

        url = self.local_server(handle)
        source = {**next(source for source in SOURCES if source['kind'] == 'huggingface'),
                  'url': url + '/models'}
        with patch('stealthprint.updates.time.monotonic', side_effect=lambda: clock[0]):
            state = refresh_monitor(self.directory, sources=(source,), now=NOW)
        self.assertEqual(requests, ['/models', '/models?page=2', '/models?page=3', '/models?page=4'])
        self.assertIn('240 seconds', state['sources'][source['id']]['error'])
        self.assertNotIn('inventory', state['sources'][source['id']])

    def test_package_metadata_and_archive_share_collection_deadline(self):
        clock, requests = [0], []
        metadata, archive = package()
        router_body = json.dumps({'data': [{'id': 'kept', 'name': 'kept'}]}).encode()

        def handle(request):
            requests.append(request.path)
            clock[0] += 70
            body = (json.dumps(metadata).encode() if request.path == '/metadata' else
                    archive if request.path == '/archive' else router_body)
            request.send_response(200)
            request.send_header('Content-Length', str(len(body)))
            request.end_headers()
            request.wfile.write(body)

        url = self.local_server(handle)
        open_url = urllib.request.urlopen

        def local_open(request, **kwargs):
            path = ('/metadata' if request.full_url == SOURCE['url'] else
                    '/archive' if request.full_url == metadata['dist']['tarball'] else
                    request.full_url.removeprefix(url))
            return open_url(urllib.request.Request(url + path, headers=request.headers), **kwargs)

        sources = tuple({**ROUTER, 'id': 'router-' + str(index), 'url': url + '/router-' + str(index)}
                        for index in range(2)) + (SOURCE,)
        with patch('stealthprint.updates.time.monotonic', side_effect=lambda: clock[0]), \
                patch('stealthprint.updates.urllib.request.urlopen', side_effect=local_open):
            state = refresh_monitor(self.directory, sources=sources, now=NOW)
        self.assertEqual(requests, ['/router-0', '/router-1', '/metadata', '/archive'])
        self.assertIsNone(state['sources']['router-0']['error'])
        self.assertIsNone(state['sources']['router-1']['error'])
        self.assertIn('240 seconds', state['sources'][SOURCE['id']]['error'])
        self.assertNotIn('inventory', state['sources'][SOURCE['id']])

    def test_candidate_labels_distinguish_sdk_additions_and_metadata(self):
        event = {'kind': 'changed', 'entity_id': 'types', 'before': {'models': ['known']},
                 'after': {'models': ['known', 'new']}}
        self.assertEqual(candidate_ids(event, 'sdk', []), ['new'])
        self.assertEqual(candidate_ids(event, 'openrouter', []), [])
        self.assertEqual(candidate_ids({**event, 'kind': 'added'}, 'tracker', []), [])
        self.assertEqual(candidate_ids({**event, 'kind': 'added'}, 'huggingface', []), ['types'])


if __name__ == '__main__':
    unittest.main()
