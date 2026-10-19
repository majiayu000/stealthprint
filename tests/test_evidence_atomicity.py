"""Evidence publication stays recoverable after interrupted local writes."""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stealthprint.updates import _save_evidence


class EvidenceAtomicityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.envelope = {'responses': [{'body': 'example'}]}
        self.body = json.dumps(self.envelope, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
        self.path = self.directory / 'evidence' / (hashlib.sha256(self.body).hexdigest() + '.json.gz')

    def assert_saved(self):
        relative = _save_evidence(self.directory, self.envelope)
        self.assertEqual(self.directory / relative, self.path)
        self.assertEqual(gzip.decompress(self.path.read_bytes()), self.body)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_partial_write_is_not_published_and_retry_succeeds(self):
        write_bytes = Path.write_bytes

        def interrupted(path, data):
            write_bytes(path, data[:10])
            raise OSError('interrupted evidence write')

        with patch.object(Path, 'write_bytes', interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted evidence write'):
                _save_evidence(self.directory, self.envelope)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])
        self.assert_saved()

    def test_existing_truncated_or_wrong_content_is_repaired(self):
        for damaged in [b'', gzip.compress(self.body)[:10], gzip.compress(b'wrong envelope')]:
            with self.subTest(damaged=damaged):
                self.path.parent.mkdir(exist_ok=True)
                self.path.write_bytes(damaged)
                self.assert_saved()

    def test_valid_file_is_reused_without_writing(self):
        self.assert_saved()
        with patch.object(Path, 'write_bytes', side_effect=AssertionError('unexpected rewrite')):
            self.assert_saved()

    def test_replace_failure_keeps_old_file_and_cleans_temporary(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b'previous partial file')
        with patch.object(Path, 'replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                _save_evidence(self.directory, self.envelope)
        self.assertEqual(self.path.read_bytes(), b'previous partial file')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assert_saved()


if __name__ == '__main__':
    unittest.main()
