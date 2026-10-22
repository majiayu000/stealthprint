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
        self.builder.MONITOR_DIR = Path(temporary.name) / 'monitor'

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.builder.main()

    def test_asset_urls_change_with_content_to_avoid_stale_styles(self):
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            (site / 'styles.css').write_text('old styles')
            (site / 'site.js').write_text('unchanged script')
            with mock.patch.object(self.builder, 'SITE', site):
                self.builder.render_page('index.html', 'Title', 'Description', '<h1>Title</h1>')
                before = (self.builder.OUT / 'index.html').read_text()
                (site / 'styles.css').write_text('new card layout')
                self.builder.render_page('index.html', 'Title', 'Description', '<h1>Title</h1>')
                after = (self.builder.OUT / 'index.html').read_text()
            old_version = hashlib.sha256(b'old styles').hexdigest()[:12]
            new_version = hashlib.sha256(b'new card layout').hexdigest()[:12]
            script_version = hashlib.sha256(b'unchanged script').hexdigest()[:12]
            self.assertIn(f'assets/styles.css?v={old_version}', before)
            self.assertIn(f'assets/styles.css?v={new_version}', after)
            self.assertNotIn(f'assets/styles.css?v={old_version}', after)
            for html in (before, after):
                self.assertIn(f'assets/site.js?v={script_version}', html)

    def test_links_headings_downloads_and_stale_output(self):
        root = self.builder.OUT
        root.mkdir()
        (root / 'removed-case.html').write_text('stale')
        self.build()
        self.assertFalse((root / 'removed-case.html').exists())
        pages = {p.resolve(): Document(p.read_text()) for p in root.rglob('*.html')}
        self.assertEqual(len(pages), 9)
        for path, doc in pages.items():
            with self.subTest(page=path.name):
                self.assertEqual(doc.h1, 1)
                self.assertEqual(len(doc.ids), len(set(doc.ids)))
                for href in doc.links:
                    url = urlsplit(href)
                    if url.scheme or url.netloc:
                        continue
                    if url.path.startswith('/'):
                        destination = (root / unquote(url.path).lstrip('/')).resolve()
                    else:
                        destination = (path.parent / unquote(url.path)).resolve() if url.path else path
                    if destination.is_dir():
                        destination /= 'index.html'
                    self.assertTrue(destination.exists(), href)
                    if url.fragment and destination in pages:
                        self.assertIn(unquote(url.fragment), pages[destination].ids, href)
        for file in (root / 'data').glob('*.json'):
            self.assertEqual(file.read_bytes(), (self.builder.ROOT / 'docs' / file.name).read_bytes())
        self.assertTrue((root / 'assets/build.json').exists())
        self.assertEqual((root / 'assets/updates.json').read_bytes(),
                         (self.builder.SITE / 'updates.json').read_bytes())

    def test_monitor_state_evidence_and_failure_are_published_without_git_metadata(self):
        import gzip
        import json
        directory = self.builder.MONITOR_DIR
        (directory / 'evidence').mkdir(parents=True)
        (directory / '.git').mkdir()
        (directory / '.git/config').write_text('private checkout configuration')
        body = gzip.compress(b'{"responses": []}')
        evidence = 'evidence/example.json.gz'
        (directory / evidence).write_bytes(body)
        state = {'checked_at': '2026-10-20T00:00:00Z', 'events': [], 'sources': {
            'openrouter': {'last_success_at': '2026-10-20T00:00:00Z', 'inventory': {'a': {}},
                           'error': None, 'evidence': evidence},
            'aitracker-feed': {'error': '<script>bad upstream</script>'},
        }}
        state['events'] = [{
            'id': 'change-event', 'source_id': 'openrouter', 'entity_id': 'model/a', 'kind': 'changed',
            'observed_at': '2026-10-20T00:00:00Z', 'published_at': None,
            'before': {'name': 'model/a', 'url': 'https://openrouter.ai/model/a', 'context_length': 10},
            'after': {'name': '<script>model</script>', 'url': 'https://openrouter.ai/model/a', 'context_length': 20},
            'before_evidence': evidence, 'evidence': evidence,
        }]
        (directory / 'state.json').write_text(json.dumps(state))
        self.build()
        root = self.builder.OUT
        self.assertEqual((root / 'data/monitor' / evidence).read_bytes(), body)
        self.assertFalse((root / 'data/monitor/.git').exists())
        self.assertEqual(json.loads((root / 'data/monitor/state.json').read_text()), state)
        html = (root / 'updates/index.html').read_text()
        self.assertIn('data-source-health', html)
        self.assertIn('核对前后记录', html)
        self.assertIn('本站观察时间 · 2026-10-20 08:00', html)
        self.assertNotIn('<script>model</script>', html)
        self.assertIn('&lt;script&gt;model&lt;/script&gt;', html)
        self.assertIn('data-checked-at="2026-10-20T00:00:00Z"', html)
        self.assertIn('采集失败', html)
        self.assertNotIn('<script>bad upstream</script>', html)
        self.assertIn('&lt;script&gt;bad upstream&lt;/script&gt;', html)
        self.assertIn('../data/monitor/evidence/example.json.gz', html)

    def test_update_timeline_escapes_source_text_and_keeps_stable_links(self):
        snapshot = {
            'fetched_at': '2026-10-17T12:00:00Z',
            'catalog': {'pages': [{'path': '/example', 'model': '<script>model</script>', 'maker': 'Lab'}]},
            'feed': {'items': [
                {'id': 'release', 'url': 'https://live.aitracker.bot/example', 'date_published': '2026-10-17T08:00:00Z',
                 'title': 'release', 'tags': ['release'], 'content_text': '<img src=x onerror=alert(1)>'},
                {'id': 'leak', 'url': 'https://live.aitracker.bot/example', 'date_published': '2026-10-17T01:00:00Z',
                 'title': 'leak', 'tags': ['leak'], 'content_text': 'earlier leak'},
            ]},
        }
        with mock.patch.object(self.builder.json, 'loads', return_value=snapshot):
            self.builder.updates()
        text = (self.builder.OUT / 'updates/index.html').read_text()
        self.assertNotIn('<script>model</script>', text)
        self.assertIn('&lt;script&gt;model&lt;/script&gt;', text)
        self.assertNotIn('<img src=x', text)
        self.assertIn('查看消息经过 · 2 条', text)
        history = text.split('<ol>', 1)[1]
        self.assertLess(history.index('earlier leak'), history.index('&lt;img'))
        self.assertIn('2026-10-17 16:00', text)
        self.assertIn('本站复测 · 暂无', text)
        anchors = [value for value in Document(text).ids if value.startswith('update-')]
        snapshot['feed']['items'].reverse()
        with mock.patch.object(self.builder.json, 'loads', return_value=snapshot):
            self.builder.updates()
        changed = (self.builder.OUT / 'updates/index.html').read_text()
        self.assertEqual(anchors, [value for value in Document(changed).ids if value.startswith('update-')])

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

    def test_candidate_filter_keeps_prior_signal_when_latest_event_is_metadata(self):
        import json
        sdk_before = {'name': 'chat_model.py', 'url': 'https://example.com/types', 'models': ['known']}
        sdk_after = {**sdk_before, 'models': ['known', '<new-model>']}
        events = [
            {'id': 'new-sdk-id', 'source_id': 'openai-sdk', 'entity_id': 'model-types', 'kind': 'changed',
             'before': sdk_before, 'after': sdk_after, 'observed_at': '2026-10-22T00:00:00Z', 'published_at': None},
            {'id': 'notes-only', 'source_id': 'openai-sdk', 'entity_id': 'model-types', 'kind': 'changed',
             'before': sdk_after, 'after': {**sdk_after, 'notes': ['documentation correction']},
             'observed_at': '2026-10-22T01:00:00Z', 'published_at': None},
            {'id': 'build-id', 'source_id': 'gemini-cli-build', 'entity_id': 'gemini-example', 'kind': 'added',
             'before': None, 'after': {'name': 'gemini-example', 'url': 'https://example.com/build',
                                      'symbols': ['PREVIEW_MODEL'], 'file': 'package/dist/src/config/models.js'},
             'observed_at': '2026-10-22T02:00:00Z', 'published_at': None},
        ]
        directory = self.builder.MONITOR_DIR
        directory.mkdir()
        (directory / 'state.json').write_text(json.dumps({'checked_at': '2026-10-22T02:00:00Z',
                                                         'sources': {}, 'events': events}))
        self.build()
        html = (self.builder.OUT / 'updates/index.html').read_text()
        self.assertIn('id="update-signal"', html)
        self.assertIn('data-signals="candidate change"', html)
        self.assertIn('含新标识线索 · &lt;new-model&gt;', html)
        self.assertIn('新标识 · 待核对', html)
        self.assertNotIn('<new-model>', html)
        self.assertIn('PREVIEW_MODEL', html)
        self.assertIn('package/dist/src/config/models.js', html)
        self.assertIn('不代表正式发布或实际可调用', html)


if __name__ == '__main__':
    unittest.main()
