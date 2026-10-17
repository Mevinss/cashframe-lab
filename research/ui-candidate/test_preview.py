"""Synthetic, socket-free checks of preview containment and UI references."""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('candidate_preview', ROOT / 'preview.py')
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.refs, self.labels, self.sources = [], [], [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'label' and 'for' in attrs:
            self.labels.append(attrs['for'])
        if 'aria-labelledby' in attrs:
            self.refs.extend(attrs['aria-labelledby'].split())
        if 'src' in attrs:
            self.sources.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') != 'icon':
            self.sources.append(attrs['href'])


class PreviewTests(unittest.TestCase):
    def test_ui_assets_only_and_no_directory_listing(self):
        self.assertEqual(preview.resolve_asset('/')[0], ROOT / 'index.html')
        for name in preview.ASSETS:
            self.assertIsNotNone(preview.resolve_asset('/' + name))
        for path in ('/../.env', '/%2e%2e/.env', '/%252e%252e/.env', '/src/cashframe/api.py',
                     '/.git/config', '/research/', '/README.md', '/preview.py', '/local/',
                     '/app.mjs/../README.md', '//index.html', '/%2fetc/passwd'):
            self.assertIsNone(preview.resolve_asset(path))

    def test_no_outbound_connections_or_upload_policy(self):
        self.assertIn("connect-src 'self'", preview.CSP)
        self.assertIn("form-action 'none'", preview.CSP)
        self.assertIn("media-src blob: 'self'", preview.CSP)

    def test_markup_references_and_local_assets(self):
        parser = Markup()
        markup = (ROOT / 'index.html').read_text()
        parser.feed(markup)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for reference in parser.refs + parser.labels:
            self.assertIn(reference, parser.ids)
        for source in parser.sources:
            self.assertTrue(source.startswith('./'))
            self.assertTrue((ROOT / source[2:]).is_file())
        self.assertIn('Демо — синтетические данные', markup)
        self.assertNotIn('https://', markup)


if __name__ == '__main__':
    unittest.main()
