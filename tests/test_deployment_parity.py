import json
import unittest

import httpx

from cashframe.deployment_parity import compare_deployments, PUBLIC_PATHS


class DeploymentParityTests(unittest.TestCase):
    def transport(self, *, different=None, redirect=None, oversized=None, broken_schema=False):
        self.seen = []

        def handle(request):
            self.seen.append(request)
            path, target = request.url.path, request.url.host == 'audit.team.kz'
            if target and path == redirect:
                return httpx.Response(302, headers={'location': 'https://untrusted.invalid/secret'})
            if target and path == oversized:
                return httpx.Response(200, content=b'x' * 1025)
            if path == '/health/ready':
                return httpx.Response(200, json={'ready': True, 'private': 'PRIVATE DATA'})
            if path == '/openapi.json':
                return httpx.Response(200, json={'paths': {} if not broken_schema else 'invalid',
                                                'openapi': '3.1.0', 'components': {}})
            if path == '/api/v1/system':
                if request.headers.get('X-API-Key') != 'fixture-secret':
                    return httpx.Response(401)
                return httpx.Response(200, json={'ready': True, 'model_profile': 'experiment-v2',
                                                'private': 'PRIVATE DATA', 'key': 'fixture-secret'})
            content = ('different' if target and path == different else path).encode()
            return httpx.Response(200, content=content, headers={'set-cookie': 'session=unsafe'})

        return httpx.MockTransport(handle)

    def test_public_parity_is_read_only_and_does_not_certify_weights(self):
        report = compare_deployments('http://127.0.0.1:8000', 'https://audit.team.kz',
                                     transport=self.transport())
        self.assertTrue(report['frontend_matches'])
        self.assertTrue(report['api_schema_matches'])
        self.assertTrue(report['both_ready'])
        self.assertFalse(report['runtime_weights_identity_verified'])
        self.assertEqual(set(report['assets']), set(PUBLIC_PATHS))
        self.assertTrue(all(request.method == 'GET' for request in self.seen))
        self.assertTrue(all('cookie' not in request.headers and 'x-api-key' not in request.headers
                            for request in self.seen))
        self.assertFalse(any('/overview' in request.url.path or '/jobs' in request.url.path
                             or '/dataset-review' in request.url.path for request in self.seen))
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_changed_script_identifies_mismatch_even_when_html_matches(self):
        report = compare_deployments('http://localhost:8000', 'https://audit.team.kz',
                                     transport=self.transport(different='/static/app.js'))
        self.assertFalse(report['frontend_matches'])
        self.assertTrue(report['assets']['/']['matches'])
        self.assertFalse(report['assets']['/static/app.js']['matches'])

    def test_redirect_is_never_followed_or_forwarded_a_key(self):
        report = compare_deployments('http://localhost:8000', 'https://audit.team.kz', key='fixture-secret',
                                     transport=self.transport(redirect='/api/v1/system'))
        self.assertTrue(report['auth']['reference']['accepted'])
        self.assertFalse(report['auth']['target']['accepted'])
        self.assertFalse(any(request.url.host == 'untrusted.invalid' for request in self.seen))
        self.assertNotIn('fixture-secret', json.dumps(report))
        self.assertTrue(all('cookie' not in request.headers for request in self.seen))

    def test_insecure_nonlocal_or_secret_urls_rejected_without_echo(self):
        for bad in ['http://audit.team.kz', 'https://key:secret@audit.team.kz',
                    'https://audit.team.kz/?secret=yes', 'file:///private/secret',
                    'https://audit.team.kz/api', 'https://audit.team.kz/#secret']:
            with self.subTest(url=bad), self.assertRaises(ValueError) as error:
                compare_deployments('http://localhost:8000', bad, transport=self.transport())
            self.assertNotIn('secret', str(error.exception))

    def test_oversized_and_invalid_schema_fail_closed(self):
        report = compare_deployments('http://localhost:8000', 'https://audit.team.kz',
                                     transport=self.transport(oversized='/static/app.js', broken_schema=True),
                                     max_bytes=1024)
        self.assertFalse(report['frontend_matches'])
        self.assertEqual(report['assets']['/static/app.js']['target']['error'], 'response_too_large')
        self.assertFalse(report['api_schema_matches'])

    def test_network_failures_never_emit_exception_details(self):
        def fail(request):
            raise httpx.ConnectError('PRIVATE fixture-secret', request=request)
        report = compare_deployments('http://localhost:8000', 'https://audit.team.kz',
                                     transport=httpx.MockTransport(fail), key='fixture-secret')
        self.assertFalse(report['frontend_matches'])
        self.assertFalse(report['both_ready'])
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertNotIn('fixture-secret', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
