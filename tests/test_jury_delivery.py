import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from cashframe.jury_delivery import (
    build_manifest, probe_service, read_existing_key, validate_base_url,
)


class JuryDeliveryTests(unittest.TestCase):
    def responses(self, *, review=404, ready=True):
        seen = []
        def handler(request):
            seen.append((request.url.path, dict(request.headers)))
            if request.url.path == '/':
                return httpx.Response(200, text='<html>fixture</html>', headers={'set-cookie': 'session=unsafe'})
            if request.url.path == '/health/ready':
                return httpx.Response(200 if ready else 503, json={'ready': ready})
            if request.url.path == '/dataset-review':
                return httpx.Response(review, text='private fixture must not be emitted')
            if request.headers.get('X-API-Key') != 'fixture-key':
                return httpx.Response(401, json={'detail': 'private error must not be emitted'})
            return httpx.Response(200, json={
                'ready': ready, 'model_profile': 'experiment-v2',
                'type_classifier': {'status': 'absent', 'class_count': 0,
                                    'labels': ['PRIVATE LABEL'], 'secret': 'fixture-key'},
                'sensitive': 'PRIVATE DESCRIPTION',
            })
        return httpx.MockTransport(handler), seen

    def test_header_auth_is_checked_without_cookies_or_private_data(self):
        transport, seen = self.responses()
        observed = probe_service('http://127.0.0.1:8000', 'fixture-key', transport=transport)
        self.assertTrue(observed['checks']['header_auth_accepted'])
        self.assertTrue(observed['checks']['anonymous_rejected'])
        self.assertTrue(observed['checks']['wrong_key_rejected'])
        self.assertTrue(observed['checks']['private_review_disabled'])
        self.assertTrue(all('cookie' not in headers for _, headers in seen))
        encoded = json.dumps(observed)
        for forbidden in ['fixture-key', 'PRIVATE', 'private fixture', 'private error']:
            self.assertNotIn(forbidden, encoded)

    def test_enabled_training_review_blocks_technical_handoff(self):
        transport, _ = self.responses(review=200)
        report = build_manifest('http://127.0.0.1:8000',
                                probe_service('http://127.0.0.1:8000', 'fixture-key', transport=transport))
        self.assertFalse(report['technical_preflight_passed'])
        self.assertIn('private_review_enabled_or_unverified', report['submission_blockers'])

    def test_localhost_never_becomes_public_or_complete(self):
        transport, _ = self.responses()
        report = build_manifest('http://127.0.0.1:8000',
                                probe_service('http://127.0.0.1:8000', 'fixture-key', transport=transport))
        self.assertTrue(report['technical_preflight_passed'])
        self.assertIsNone(report['public_url_candidate'])
        self.assertFalse(report['submission_complete'])
        self.assertIn('public_https_url_missing', report['submission_blockers'])
        self.assertIn('accepted_type_classifier_missing', report['quality_gaps'])

    def test_public_url_still_requires_external_mov_smoke(self):
        transport, _ = self.responses()
        observations = probe_service('https://audit.team.kz', 'fixture-key', transport=transport)
        first = build_manifest('https://audit.team.kz', observations)
        self.assertEqual(first, build_manifest('https://audit.team.kz', observations))
        self.assertEqual(first['public_url_candidate'], 'https://audit.team.kz')
        self.assertFalse(first['public_reachability_verified'])
        self.assertFalse(first['submission_complete'])

    def test_unready_service_never_passes(self):
        transport, _ = self.responses(ready=False)
        report = build_manifest('http://localhost:8000',
                                probe_service('http://localhost:8000', 'fixture-key', transport=transport))
        self.assertFalse(report['technical_preflight_passed'])

    def test_redirect_is_not_followed_with_secret(self):
        seen = []
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={'location': 'https://external.invalid/'})
        observations = probe_service('http://localhost:8000', 'fixture-key', transport=httpx.MockTransport(handler))
        self.assertFalse(observations['checks']['header_auth_accepted'])
        self.assertFalse(any('external.invalid' in url for url in seen))

    def test_secret_urls_and_non_root_urls_are_rejected(self):
        for bad in ['https://key:secret@audit.team.kz', 'https://audit.team.kz/?key=secret',
                    'https://audit.team.kz/#secret', 'https://audit.team.kz/api',
                    'file:///private/key.txt', 'https://audit.team.kz/\r\nsecret']:
            with self.subTest(url=bad), self.assertRaises(ValueError) as error:
                validate_base_url(bad)
            self.assertNotIn('secret', str(error.exception))

    def test_read_key_uses_effective_setting_and_never_generates_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(read_existing_key(root, {'CASHFRAME_API_KEY': 'effective'}), 'effective')
            with self.assertRaises(ValueError):
                read_existing_key(root, {})
            self.assertFalse((root / 'local').exists())
            (root / 'local').mkdir()
            (root / 'local/api-key.txt').write_text('fixture-file-key', encoding='utf-8')
            self.assertEqual(read_existing_key(root, {}), 'fixture-file-key')

    def test_request_failures_are_safe_and_do_not_echo_url_or_key(self):
        def handler(request):
            raise httpx.ConnectError('sensitive fixture-key request detail', request=request)
        report = probe_service('http://localhost:8000', 'fixture-key', transport=httpx.MockTransport(handler))
        self.assertFalse(any(report['checks'].values()))
        self.assertNotIn('fixture-key', json.dumps(report))
        self.assertNotIn('sensitive', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
