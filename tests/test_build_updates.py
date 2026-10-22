"""Published package inspection without installation or executing JavaScript."""
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from stealthprint.updates import (GEMINI_MODELS_PATH, GEMINI_PACKAGE, SOURCES,
                                 _fetch, candidate_ids, refresh_monitor)


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
        self.assertEqual(candidate_ids(event, 'gemini-build'), ['gemini-example-flash'])
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
        self.assertEqual(candidate_ids(restored['events'][-1], 'gemini-build'), [])

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

    def test_streamed_download_deadline_and_truncation(self):
        for length, clock, message in [('12', [0, 1, 2], 'truncated'), ('5', [0, 91], '90 seconds')]:
            with self.subTest(message=message):
                stream = io.BytesIO(b'hello')
                stream.code, stream.headers = 200, {'Content-Length': length}
                with patch('stealthprint.updates.urllib.request.urlopen', return_value=stream), \
                        patch('stealthprint.updates.time.monotonic', side_effect=clock):
                    with self.assertRaisesRegex((ValueError, TimeoutError), message):
                        _fetch('https://example.com/file', {})

    def test_candidate_labels_distinguish_sdk_additions_and_metadata(self):
        event = {'kind': 'changed', 'entity_id': 'types', 'before': {'models': ['known']},
                 'after': {'models': ['known', 'new']}}
        self.assertEqual(candidate_ids(event, 'sdk'), ['new'])
        self.assertEqual(candidate_ids(event, 'openrouter'), [])
        self.assertEqual(candidate_ids({**event, 'kind': 'added'}, 'tracker'), [])
        self.assertEqual(candidate_ids({**event, 'kind': 'added'}, 'huggingface'), ['types'])


if __name__ == '__main__':
    unittest.main()
