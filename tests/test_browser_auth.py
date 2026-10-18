from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from cashframe.api import create_app
from cashframe.config import Settings


class BrowserAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(root=Path(self.temp.name), api_key='shared-jury-fixture'), analyzer=lambda *a: {})

    def test_shared_key_establishes_private_session_and_header_api_still_works(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/api/v1/overview').status_code, 401)
            self.assertEqual(client.post('/api/v1/session', headers={'X-API-Key':'wrong'}).status_code, 401)
            response = client.post('/api/v1/session', headers={'X-API-Key':'shared-jury-fixture'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'authenticated':True})
            self.assertNotIn('shared-jury-fixture', response.text + response.headers.get('set-cookie',''))
            self.assertIn('HttpOnly', response.headers['set-cookie'])
            self.assertIn('SameSite=strict', response.headers['set-cookie'])
            self.assertEqual(client.get('/api/v1/overview').status_code, 200)
            self.assertEqual(client.get('/api/v1/overview', headers={'X-API-Key':'wrong'}).status_code, 401)
            self.assertEqual(client.delete('/api/v1/session').status_code, 200)
            self.assertEqual(client.get('/api/v1/overview').status_code, 401)
            self.assertEqual(client.get('/api/v1/overview', headers={'X-API-Key':'shared-jury-fixture'}).status_code, 200)

    def test_foreign_origin_cannot_change_authenticated_session(self):
        with TestClient(self.app) as client:
            client.post('/api/v1/session', headers={'X-API-Key':'shared-jury-fixture'})
            self.assertEqual(client.delete('/api/v1/session', headers={'Origin':'https://foreign.example'}).status_code, 403)
            self.assertEqual(client.get('/api/v1/session').status_code, 200)
            self.assertEqual(client.delete('/api/v1/session', headers={'Origin':'http://testserver'}).status_code, 200)

    def test_random_cookie_cannot_access_api(self):
        with TestClient(self.app) as client:
            client.cookies.set('cashframe_session', 'invented')
            self.assertEqual(client.get('/api/v1/session').status_code, 401)

    def test_browser_session_does_not_allow_minting_sessions_without_shared_key(self):
        with TestClient(self.app) as client:
            response=client.post('/api/v1/session', headers={'X-API-Key':'shared-jury-fixture'})
            old_token=client.cookies.get('cashframe_session')
            self.assertEqual(client.post('/api/v1/session').status_code,401)
            self.assertEqual(client.delete('/api/v1/session').status_code,200)
            client.cookies.set('cashframe_session',old_token)
            self.assertEqual(client.get('/api/v1/overview').status_code,401)

    def test_https_browser_cookie_is_secure_and_session_expires(self):
        from unittest.mock import patch
        with TestClient(self.app,base_url='https://testserver') as client:
            with patch('cashframe.browser_auth.time') as clock:
                clock.monotonic.return_value=100
                response=client.post('/api/v1/session',headers={'X-API-Key':'shared-jury-fixture'})
                self.assertIn('Secure',response.headers['set-cookie'])
                self.assertEqual(client.get('/api/v1/session').status_code,200)
            with patch('cashframe.browser_auth.time') as clock:
                clock.monotonic.return_value=28901
                self.assertEqual(client.get('/api/v1/session').status_code,401)


if __name__ == '__main__':
    unittest.main()
