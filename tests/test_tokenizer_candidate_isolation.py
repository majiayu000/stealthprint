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
        def load(path):
            data = json.loads(Path(path).read_text())
            if data.get('unsupported'):
                raise Exception('unsupported tokenizer model')
            return data
        return {
            'tokenizers': types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_file=load)),
            'tiktoken': types.SimpleNamespace(get_encoding=lambda name: name),
        }

    def test_bad_candidates_are_diagnosed_without_discarding_valid_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.json').write_text('{')
            (root / 'unsupported.json').write_text('{"unsupported": true}')
            (root / 'valid.json').write_text('{"model": "synthetic"}')
            with mock.patch.dict('sys.modules', self.modules()), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                local = layers.load_local_tokenizers(directory)
            self.assertEqual(set(local), {'valid', 'o200k_base', 'cl100k_base'})
            self.assertEqual(len(caught), 2)
            self.assertTrue(any('bad.json' in str(w.message) for w in caught))
            self.assertTrue(any('unsupported.json' in str(w.message) for w in caught))
            with mock.patch('importlib.metadata.version', return_value='synthetic'):
                sources = layers.tokenizer_sources(directory, local)
            self.assertEqual(set(sources), set(local))
            self.assertEqual(sources['valid']['sha256'], hashlib.sha256((root / 'valid.json').read_bytes()).hexdigest())

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
