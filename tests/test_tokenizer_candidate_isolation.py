"""Local candidate failures must not discard unrelated comparison evidence."""
import hashlib
import json
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest import mock

from stealthprint import layers, survey


class CandidateIsolationTests(unittest.TestCase):
    def modules(self):
        def load(serialized):
            data = json.loads(serialized)
            if data.get('unsupported'):
                raise Exception('unsupported tokenizer model')
            return data
        def load_file(path):
            with open(path, encoding='utf-8') as stream:
                return load(stream.read())
        return {
            'tokenizers': types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_str=load, from_file=load_file)),
            'tiktoken': types.SimpleNamespace(get_encoding=lambda name: name),
        }

    def test_bad_candidates_are_diagnosed_without_discarding_valid_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.json').write_text('{')
            (root / 'unsupported.json').write_text('{"unsupported": true}')
            (root / 'valid.json').write_text('{"model": "synthetic"}')
            with mock.patch.dict('sys.modules', self.modules()), self.assertLogs(layers.__name__, level='WARNING') as caught, warnings.catch_warnings():
                warnings.simplefilter('error')
                local = layers.load_local_tokenizers(directory)
            self.assertEqual(set(local), {'valid', 'o200k_base', 'cl100k_base'})
            self.assertEqual(len(caught.records), 2)
            self.assertTrue(any('bad.json' in w.getMessage() for w in caught.records))
            self.assertTrue(any('unsupported.json' in w.getMessage() for w in caught.records))
            with mock.patch('importlib.metadata.version', return_value='synthetic'):
                sources = layers.tokenizer_sources(directory, local)
            self.assertEqual(set(sources), set(local))
            self.assertEqual(sources['valid']['sha256'], hashlib.sha256((root / 'valid.json').read_bytes()).hexdigest())

    def test_file_access_failures_are_fatal(self):
        for error in (PermissionError('unreadable'), FileNotFoundError('removed'),
                      OSError('transient read failure')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'locked.json').write_text('{}')
                (root / 'sources.lock').write_text('{"locked": {"sha256": "expected"}}')
                with mock.patch.dict('sys.modules', self.modules()), mock.patch(
                        'builtins.open', side_effect=error):
                    with self.assertRaises(type(error)):
                        layers.load_local_tokenizers(directory)

    def test_read_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'valid.json').write_text('{}')
            stream = mock.mock_open()
            stream.return_value.read.side_effect = OSError('read failed')
            with mock.patch.dict('sys.modules', self.modules()), mock.patch('builtins.open', stream):
                with self.assertRaisesRegex(OSError, 'read failed'):
                    layers.load_local_tokenizers(directory)

    def test_real_tokenizer_serialization_without_downloads(self):
        try:
            from tokenizers import Tokenizer, models
        except ImportError:
            self.skipTest('optional tokenizers dependency is not installed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Tokenizer(models.WordLevel({'[UNK]': 0, 'hello': 1}, unk_token='[UNK]')).save(
                str(root / 'valid.json'))
            (root / 'bad.json').write_text('{')
            (root / 'unsupported.json').write_text('{"model": {"type": "unsupported"}}')
            # Stub only built-in encodings: no tokenizer downloads or model calls.
            with mock.patch.dict('sys.modules', {'tiktoken': self.modules()['tiktoken']}), self.assertLogs(
                    layers.__name__, level='WARNING'), warnings.catch_warnings():
                warnings.simplefilter('error')
                local = layers.load_local_tokenizers(directory)
            self.assertEqual(set(local), {'valid', 'o200k_base', 'cl100k_base'})
            self.assertEqual(local['valid'].encode('hello').ids, [1])

    def test_invalid_utf8_is_a_candidate_format_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.json').write_bytes(b'\xff')
            (root / 'valid.json').write_text('{}')
            with mock.patch.dict('sys.modules', self.modules()), self.assertLogs(
                    layers.__name__, level='WARNING') as caught:
                local = layers.load_local_tokenizers(directory)
            self.assertEqual(set(local), {'valid', 'o200k_base', 'cl100k_base'})
            self.assertIn('bad.json', caught.output[0])

    def test_changed_successful_candidate_still_invalidates_survey_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / 'valid.json'
            candidate.write_text('{"model": "first"}')
            with mock.patch.dict('sys.modules', self.modules()), mock.patch('importlib.metadata.version', return_value='synthetic'):
                before = layers.tokenizer_sources(directory, layers.load_local_tokenizers(directory))
                saved = root / 'survey.jsonl'
                saved.write_text(json.dumps({'model': 'synthetic', 'complete': True, 'experiment': {'tokenizers': before}}) + '\n')
                candidate.write_text('{"model": "second"}')
                after = layers.tokenizer_sources(directory, layers.load_local_tokenizers(directory))
            with self.assertRaisesRegex(ValueError, 'configuration differs'):
                survey._done_models(saved, {'tokenizers': after})

    def test_missing_dependency_still_fails(self):
        with mock.patch.dict('sys.modules', {'tokenizers': None}):
            with self.assertRaises(RuntimeError):
                layers.load_local_tokenizers('unused')

    def test_invalid_provenance_manifest_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'sources.lock').write_text('{')
            with self.assertRaises(json.JSONDecodeError):
                layers.tokenizer_sources(directory, {})


if __name__ == '__main__':
    unittest.main()
