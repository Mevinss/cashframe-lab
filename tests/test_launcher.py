from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cashframe import launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_shared_local_and_process_settings_precedence(self):
        (self.root / '.env').write_text('CASHFRAME_HOST=127.0.0.1\nCASHFRAME_PORT=8000\n', encoding='utf-8')
        (self.root / '.env.local').write_text('CASHFRAME_PORT=8100\nCASHFRAME_API_KEY="local secret"\n', encoding='utf-8')
        values = launcher.load_environment(self.root, {'CASHFRAME_PORT': '8200'})
        self.assertEqual(values['CASHFRAME_HOST'], '127.0.0.1')
        self.assertEqual(values['CASHFRAME_PORT'], '8200')
        self.assertEqual(values['CASHFRAME_API_KEY'], 'local secret')

    def test_values_are_literal_and_unknown_variables_rejected(self):
        (self.root / '.env.local').write_text('CASHFRAME_API_KEY=$(do-not-run)${HOME}\n', encoding='utf-8')
        self.assertEqual(launcher.load_environment(self.root, {})['CASHFRAME_API_KEY'], '$(do-not-run)${HOME}')
        (self.root / '.env.local').write_text('PYTHONPATH=elsewhere\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '.env.local:1'):
            launcher.load_environment(self.root, {})

    def test_invalid_config_reports_location_without_secret(self):
        (self.root / '.env.local').write_text('this-is-a-private-secret\n', encoding='utf-8')
        with self.assertRaises(ValueError) as error:
            launcher.load_environment(self.root, {})
        self.assertIn('.env.local:1', str(error.exception))
        self.assertNotIn('private-secret', str(error.exception))

    def test_key_created_once_or_explicitly_supplied(self):
        key = launcher.resolve_api_key(self.root, '')
        self.assertGreaterEqual(len(key), 40)
        self.assertEqual(launcher.resolve_api_key(self.root, ''), key)
        self.assertEqual(launcher.resolve_api_key(self.root, 'external'), 'external')
        self.assertEqual((self.root / 'local/api-key.txt').read_text(), key)

    def test_empty_existing_key_is_rejected(self):
        (self.root / 'local').mkdir()
        (self.root / 'local/api-key.txt').write_text('  ')
        with self.assertRaisesRegex(ValueError, 'api-key.txt'):
            launcher.resolve_api_key(self.root, '')


if __name__ == '__main__':
    unittest.main()
