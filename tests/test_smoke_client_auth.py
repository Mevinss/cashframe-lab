"""The jury smoke client must use the server's effective key without changing it."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('smoke_client', Path(__file__).resolve().parents[1] / 'scripts/smoke_client.py')
smoke_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_client)


class SmokeClientAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'local').mkdir()

    def test_cli_sends_effective_configured_key_when_persistent_file_is_stale(self):
        (self.root / 'local/api-key.txt').write_text('stale-fixture-key', encoding='utf-8')
        (self.root / '.env.local').write_text('CASHFRAME_API_KEY=configured-fixture-key\n', encoding='utf-8')
        video = self.root / 'synthetic.mov'
        video.write_bytes(b'synthetic: transport stops before upload')
        transport = Mock()
        transport.__enter__ = Mock(return_value=transport)
        transport.__exit__ = Mock(return_value=False)
        transport.get.return_value.status_code = 401
        class StopAfterAuth(Exception):
            pass
        def check_credentials(*args, **kwargs):
            self.assertEqual(kwargs['headers']['X-API-Key'], 'configured-fixture-key')
            raise StopAfterAuth()
        transport.post.side_effect = check_credentials
        # Stop at the transport boundary: no video or secret leaves the test.
        with patch.dict('os.environ', {}, clear=True), patch('cashframe.config.ROOT', self.root), \
                patch('sys.argv', ['smoke_client.py', str(video)]), \
                patch.object(smoke_client.httpx, 'Client', return_value=transport), \
                patch.object(smoke_client, 'Path', wraps=Path) as paths:
            # The old default is relative to cwd, as a normal repository invocation.
            paths.side_effect = lambda value: self.root / value if value == 'local/api-key.txt' else Path(value)
            with self.assertRaises(StopAfterAuth):
                smoke_client.main()

    def test_effective_env_file_wins_over_stale_persistent_key(self):
        persistent = self.root / 'local/api-key.txt'
        persistent.write_text('stale-fixture-key', encoding='utf-8')
        (self.root / '.env.local').write_text('CASHFRAME_API_KEY=configured-fixture-key\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(smoke_client.read_api_key(self.root), 'configured-fixture-key')
        self.assertEqual(persistent.read_text(encoding='utf-8'), 'stale-fixture-key')

    def test_process_environment_matches_launcher_precedence(self):
        (self.root / '.env.local').write_text('CASHFRAME_API_KEY=file-fixture-key\n', encoding='utf-8')
        with patch.dict('os.environ', {'CASHFRAME_API_KEY': 'process-fixture-key'}, clear=True):
            self.assertEqual(smoke_client.read_api_key(self.root), 'process-fixture-key')

    def test_explicit_key_file_overrides_environment(self):
        chosen = self.root / 'selected-key.txt'
        chosen.write_text(' explicit-fixture-key\n', encoding='utf-8')
        with patch.dict('os.environ', {'CASHFRAME_API_KEY': 'process-fixture-key'}, clear=True):
            self.assertEqual(smoke_client.read_api_key(self.root, chosen), 'explicit-fixture-key')

    def test_existing_persistent_key_is_read_as_fallback(self):
        persistent = self.root / 'local/api-key.txt'
        persistent.write_text('persistent-fixture-key\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(smoke_client.read_api_key(self.root), 'persistent-fixture-key')

    def test_missing_or_blank_key_is_actionable_and_never_generated(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'CASHFRAME_API_KEY|key-file'):
                smoke_client.read_api_key(self.root)
            self.assertFalse((self.root / 'local/api-key.txt').exists())
            blank = self.root / 'blank-key.txt'
            blank.write_text(' \n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'empty|blank|пуст'):
                smoke_client.read_api_key(self.root, blank)
            self.assertEqual(blank.read_text(encoding='utf-8'), ' \n')


if __name__ == '__main__':
    unittest.main()
