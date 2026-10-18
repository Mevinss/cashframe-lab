import contextlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from cashframe.jury_access_export import export_jury_access, validate_access_root


class JuryAccessExportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'local').mkdir()
        self.keyfile = self.root / 'local/api-key.txt'
        self.keyfile.write_text('fixture-secret', encoding='utf-8')
        self.seen = []

    def transport(self, status=200, *, redirect=False, huge=False, ready=True):
        def handle(request):
            self.seen.append(request)
            if redirect:
                return httpx.Response(302, headers={'location': 'https://untrusted.invalid/'})
            if huge:
                return httpx.Response(200, content=b'x' * 65537)
            return httpx.Response(status, json={'ready': ready, 'private': 'PRIVATE RESPONSE'})
        return httpx.MockTransport(handle)

    def export(self, **kwargs):
        with patch.dict('os.environ', {}, clear=True):
            return export_jury_access(self.root, 'https://audit.team.kz/', **kwargs)

    def test_existing_key_exported_to_fixed_private_path_without_rotation(self):
        result = self.export(transport=self.transport())
        self.assertEqual(Path(result['path']), self.root / 'local/jury-access.md')
        content = Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('fixture-secret', content)
        self.assertIn('https://audit.team.kz/', content)
        self.assertIn('проверен', content)
        self.assertTrue(result['verified'])
        self.assertEqual(self.keyfile.read_text(), 'fixture-secret')
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertNotIn('PRIVATE RESPONSE', content)
        self.assertEqual(len(self.seen), 1)
        request = self.seen[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.url.path, '/api/v1/system')
        self.assertEqual(request.headers['X-API-Key'], 'fixture-secret')
        self.assertNotIn('cookie', request.headers)

    def test_wrong_key_explicitly_rejected_not_declared_working(self):
        result = self.export(transport=self.transport(status=401))
        content = Path(result['path']).read_text(encoding='utf-8')
        self.assertFalse(result['verified'])
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('не принят', content)
        self.assertIn('CASHFRAME_API_KEY', content)
        self.assertIn('перезапуст', content)
        self.assertIn('fixture-secret', content)
        self.assertEqual(self.keyfile.read_text(), 'fixture-secret')

    def test_loopback_export_is_verified_for_local_address_only(self):
        with patch.dict('os.environ', {}, clear=True):
            result = export_jury_access(self.root, 'http://127.0.0.1:8000/', transport=self.transport())
        self.assertTrue(result['verified'])
        content = Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('Сайт: http://127.0.0.1:8000/', content)
        self.assertNotIn('https://audit.team.kz', content)
        self.assertEqual(self.seen[0].url.host, '127.0.0.1')

    def test_redirect_and_large_body_do_not_verify_or_leak(self):
        for transport in [self.transport(redirect=True), self.transport(huge=True), self.transport(ready=False)]:
            self.seen.clear()
            result = self.export(transport=transport)
            content = Path(result['path']).read_text(encoding='utf-8')
            self.assertFalse(result['verified'])
            self.assertIn('не подтверждён', content)
            self.assertEqual(len(self.seen), 1)
            self.assertNotIn('untrusted.invalid', content)

    def test_network_error_details_never_reach_markdown_or_summary(self):
        def fail(request):
            raise httpx.ConnectError('PRIVATE RESPONSE fixture-secret', request=request)
        result = self.export(transport=httpx.MockTransport(fail))
        content = Path(result['path']).read_text(encoding='utf-8')
        self.assertFalse(result['verified'])
        self.assertNotIn('PRIVATE RESPONSE', content)
        self.assertNotIn('PRIVATE RESPONSE', json.dumps(result))

    def test_chosen_key_is_literal_and_overrides_stale_local_key(self):
        chosen = self.root / 'selected.txt'
        chosen.write_text('abc<>&`*fixture', encoding='utf-8')
        result = self.export(key_file=chosen, transport=self.transport())
        content = Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('abc&lt;&gt;&amp;`*fixture', content)
        self.assertNotIn('abc<>&', content)
        self.assertEqual(self.seen[0].headers['X-API-Key'], 'abc<>&`*fixture')
        self.assertEqual(self.keyfile.read_text(), 'fixture-secret')
        self.assertNotIn('abc<>&', json.dumps(result))

    def test_missing_blank_or_multiline_key_is_never_generated_or_fallback(self):
        self.keyfile.unlink()
        with self.assertRaises(ValueError):
            self.export(transport=self.transport())
        self.assertFalse(self.keyfile.exists())
        self.keyfile.write_text('fixture-secret')
        blank = self.root / 'selected.txt'
        for content in ['', ' \n', 'first\nsecond']:
            blank.write_text(content)
            with self.assertRaises(ValueError):
                self.export(key_file=blank, transport=self.transport())
        self.assertFalse((self.root / 'local/jury-access.md').exists())

    def test_https_remote_and_explicit_loopback_urls_only(self):
        self.assertEqual(validate_access_root('https://audit.team.kz/'), 'https://audit.team.kz')
        self.assertEqual(validate_access_root('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000')
        self.assertEqual(validate_access_root('https://localhost:8000'), 'https://localhost:8000')
        for value in ['http://audit.team.kz',
                      'https://10.0.0.1', 'https://key:secret@audit.team.kz',
                      'https://audit.team.kz/?secret=yes', 'https://audit.team.kz/api']:
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                validate_access_root(value)
            self.assertNotIn('secret', str(error.exception))

    def test_cli_stdout_never_contains_key_and_has_no_output_override(self):
        spec = importlib.util.spec_from_file_location('export_jury_access_script',
                  Path(__file__).resolve().parents[1] / 'scripts/export_jury_access.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        result = {'path': str(self.root / 'local/jury-access.md'), 'verified': False,
                  'status': 'rejected', 'http_status': 401}
        output = io.StringIO()
        with patch.object(script, 'export_jury_access', return_value=result) as exporter, \
             patch('sys.argv', ['export_jury_access.py', '--base-url', 'https://audit.team.kz']), \
             contextlib.redirect_stdout(output):
            self.assertEqual(script.main(), 1)
        self.assertNotIn('fixture-secret', output.getvalue())
        self.assertTrue(exporter.called)


if __name__ == '__main__':
    unittest.main()
