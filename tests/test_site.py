"""Verify the built archive's navigation and its publication boundary."""
import contextlib
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.h1 = [], [], 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
        if tag == 'h1':
            self.h1 += 1


@unittest.skipUnless(importlib.util.find_spec('markdown'), 'install .[site]')
class SiteTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/build_site.py'
        spec = importlib.util.spec_from_file_location('site_builder', source)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.builder.OUT = Path(temporary.name) / 'dist'

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.builder.main()

    def test_links_headings_downloads_and_stale_output(self):
        root = self.builder.OUT
        root.mkdir()
        (root / 'removed-case.html').write_text('stale')
        self.build()
        self.assertFalse((root / 'removed-case.html').exists())
        pages = {p.resolve(): Document(p.read_text()) for p in root.rglob('*.html')}
        self.assertEqual(len(pages), 8)
        for path, doc in pages.items():
            with self.subTest(page=path.name):
                self.assertEqual(doc.h1, 1)
                self.assertEqual(len(doc.ids), len(set(doc.ids)))
                for href in doc.links:
                    url = urlsplit(href)
                    if url.scheme or url.netloc:
                        continue
                    destination = (path.parent / unquote(url.path)).resolve() if url.path else path
                    if destination.is_dir():
                        destination /= 'index.html'
                    self.assertTrue(destination.exists(), href)
                    if url.fragment and destination in pages:
                        self.assertIn(unquote(url.fragment), pages[destination].ids, href)
        for file in (root / 'data').glob('*.json'):
            self.assertEqual(file.read_bytes(), (self.builder.ROOT / 'docs' / file.name).read_bytes())
        self.assertTrue((root / 'assets/build.json').exists())

    def test_failed_build_keeps_previous_complete_site(self):
        self.build()
        root = self.builder.OUT
        before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        with mock.patch.object(self.builder, 'case_page', side_effect=ValueError('bad case data')):
            with self.assertRaises(ValueError):
                self.build()
        after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.builder.OUT, root)


if __name__ == '__main__':
    unittest.main()
