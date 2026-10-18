"""Only ephemeral loopback mocks. Never connects to production port 8000."""
import base64
import contextlib
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('audit_bridge', ROOT / 'preview.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
JOB = 'a' * 32
TOKEN = 'synthetic-only'


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.seen.append((self.command, self.path, self.headers.get('X-API-Key')))
        status, data, kind = 200, b'{}', 'application/json'
        extra = {}
        if self.path == '/health/ready':
            if self.server.slow:
                time.sleep(.1)
            status = self.server.ready_status
            data = json.dumps({'ready': status == 200}).encode()
        elif self.path == '/api/v1/rules':
            data = b'{"rules":[]}'
        elif self.path.endswith('/video'):
            if self.server.preview_failure:
                status = 409
            else:
                kind, data = 'video/mp4', b'synthetic-media'
                if self.headers.get('Range') == 'bytes=0-3':
                    status, data = 206, data[:4]
                    extra = {'Content-Range': 'bytes 0-3/15', 'Accept-Ranges': 'bytes'}
        elif self.path.endswith('/report.pdf'):
            kind, data = 'application/pdf', b'%PDF-1.4 synthetic, not an official report'
            extra = {'Content-Disposition': 'attachment; filename="synthetic.pdf"', 'X-Report-SHA256': hashlib.sha256(data).hexdigest()}
        elif self.path.endswith('/passport'):
            data = b'{"synthetic":true}'
            extra = {'X-Passport-SHA256': hashlib.sha256(data).hexdigest()}
        elif self.path.endswith('/annotations'):
            data = b'{"annotations":[]}'
        elif self.path.endswith('/review'):
            data = json.dumps({'job_id': JOB, 'status': 'done', 'stage': 'done', 'review': {'duration_sec': 10, 'events': []}}).encode()
        elif self.path == '/api/v1/jobs/' + JOB:
            self.server.polls += 1
            data = json.dumps({'job_id': JOB, 'status': 'processing'} if self.server.polls == 1 else self.server.fixture).encode()
        else:
            status = 404
        if self.path != '/health/ready' and self.headers.get('X-API-Key') != TOKEN:
            status, data = 401, b'{"detail":"SYNTHETIC_TRACE_MUST_NOT_ECHO"}'
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(data)

    def do_POST(self):
        self.server.seen.append((self.command, self.path, self.headers.get('X-API-Key')))
        length = int(self.headers.get('Content-Length', 0))
        total = 0
        while total < length:
            data = self.rfile.read(min(bridge.CHUNK, length - total))
            if not data:
                break
            total += len(data)
        self.server.received = total
        data = json.dumps({'job_id': JOB} if self.path.endswith('/analyze') else {'saved': True, 'confirmed': False}).encode()
        self.send_response(202 if self.path.endswith('/analyze') else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.mock = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
        self.mock.seen, self.mock.polls, self.mock.received = [], 0, 0
        self.mock.ready_status, self.mock.slow, self.mock.preview_failure = 200, False, False
        self.mock.fixture = json.loads((ROOT / 'synthetic_fixture.json').read_text())
        self.mock_thread = threading.Thread(target=self.mock.serve_forever, daemon=True)
        self.mock_thread.start()
        class TestServer(bridge.PreviewServer):
            backend_address = self.mock.server_address
            upstream_timeout = 1
        self.server = TestServer(('127.0.0.1', 0), bridge.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.mock.shutdown(); self.mock.server_close()
        self.thread.join(); self.mock_thread.join()

    def request(self, path, method='GET', body=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        supplied = {'X-Cashframe-Client': bridge.CLIENT_HEADER, 'X-API-Key': TOKEN}
        if headers:
            supplied.update(headers)
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_readiness_200_and_503(self):
        self.assertEqual(self.request('/bridge/health/ready')[0], 200)
        self.mock.ready_status = 503
        status, _, data = self.request('/bridge/health/ready')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data), {'ready': False})

    def test_key_and_same_origin_guards_without_sensitive_errors(self):
        status, _, body = self.request('/bridge/api/v1/rules', headers={'X-API-Key': ''})
        self.assertEqual(status, 401)
        self.assertEqual(len(self.mock.seen), 0)
        self.assertEqual(self.request('/bridge/api/v1/rules', headers={'X-API-Key': 'synthetic-invalid'})[0], 401)
        self.assertNotIn(b'SYNTHETIC_TRACE', self.request('/bridge/api/v1/rules', headers={'X-API-Key': 'synthetic-invalid'})[2])
        for headers in ({'Origin': 'https://external.invalid'}, {'Sec-Fetch-Site': 'cross-site'}, {'X-Cashframe-Client': ''}, {'Host': 'external.invalid'}):
            self.assertEqual(self.request('/bridge/api/v1/rules', headers=headers)[0], 403)

    def test_allowlist_rejects_destinations_queries_and_bad_methods(self):
        for path in ('/bridge/http://external.invalid', '/bridge/api/v1/rules?destination=elsewhere', '/bridge/api/v1/jobs/../../.env', '/bridge/api/v1/jobs/%61', '/bridge/local/model.pth'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/bridge/api/v1/rules', 'POST', b'{}')[0], 404)
        self.assertEqual(self.request('/.env')[0], 404)
        self.assertEqual(self.request('/%2e%2e/.env')[0], 404)
        self.assertEqual(self.request('/preview.py')[0], 404)
        self.assertEqual(self.mock.seen, [])

    def test_streamed_synthetic_upload_and_size_limits(self):
        body = b'--fixture\r\n' + b'x' * (2 * bridge.CHUNK + 31)
        status, _, result = self.request('/bridge/api/v1/analyze', 'POST', body, {'Content-Type': 'multipart/form-data; boundary=fixture'})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(result), {'job_id': JOB})
        self.assertEqual(self.mock.received, len(body))
        self.assertEqual(self.request('/bridge/api/v1/analyze', 'POST', b'', {'Content-Type': 'multipart/form-data; boundary=fixture', 'Content-Length': str(bridge.UPLOAD_LIMIT + 1)})[0], 413)
        self.assertEqual(self.request('/bridge/api/v1/jobs/' + JOB + '/annotations', 'POST', b'x' * 16385, {'Content-Type': 'application/json'})[0], 413)
        self.assertEqual(self.request('/bridge/api/v1/analyze', 'POST', b'{}', {'Content-Type': 'application/json'})[0], 415)

    def test_async_result_and_optional_preview_failure(self):
        path = '/bridge/api/v1/jobs/' + JOB
        self.assertEqual(json.loads(self.request(path)[2])['status'], 'processing')
        result = json.loads(self.request(path)[2])
        self.assertEqual(result['status'], 'done')
        self.mock.preview_failure = True
        self.assertEqual(self.request(path + '/video')[0], 409)
        self.assertEqual(result['verdict'], 'violation')
        self.assertEqual(json.loads(self.request(path + '/review')[2])['stage'], 'done')

    def test_range_and_download_headers(self):
        prefix = '/bridge/api/v1/jobs/' + JOB
        status, headers, data = self.request(prefix + '/video', headers={'Range': 'bytes=0-3'})
        self.assertEqual((status, data), (206, b'synt'))
        self.assertEqual(headers['Content-Range'], 'bytes 0-3/15')
        for suffix, field in (('/report.pdf', 'X-Report-SHA256'), ('/passport', 'X-Passport-SHA256')):
            status, headers, data = self.request(prefix + suffix)
            self.assertEqual(status, 200)
            self.assertEqual(headers[field], hashlib.sha256(data).hexdigest())
            self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_only_manual_mock_annotation_post(self):
        path = '/bridge/api/v1/jobs/' + JOB + '/annotations'
        self.assertEqual(json.loads(self.request(path)[2]), {'annotations': []})
        self.assertFalse(any(method == 'POST' for method, *_ in self.mock.seen))
        result = self.request(path, 'POST', b'{"synthetic":true}', {'Content-Type': 'application/json'})
        self.assertEqual(json.loads(result[2]), {'saved': True, 'confirmed': False})
        self.assertEqual(sum(method == 'POST' for method, *_ in self.mock.seen), 1)

    def test_timeout_is_sanitized(self):
        self.server.upstream_timeout = .02
        self.mock.slow = True
        status, _, body = self.request('/bridge/health/ready')
        self.assertEqual(status, 504)
        self.assertNotIn(b'Traceback', body)


class ContractCompatibilityTests(unittest.TestCase):
    def test_synthetic_fixture_matches_real_schema_and_jpeg(self):
        sys.path.insert(0, str(ROOT.parents[1] / 'src'))
        from cashframe.schemas import CompletedResult
        from cashframe.annotations import validate_annotation
        from PIL import Image
        fixture = json.loads((ROOT / 'synthetic_fixture.json').read_text())
        validated = CompletedResult(**fixture).model_dump()
        self.assertEqual(validated['status'], 'done')
        jpeg = base64.b64decode(validated['violations'][0]['evidence_frames'][0]['image_base64'], validate=True)
        with Image.open(BytesIO(jpeg)) as image:
            image.load()
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (2, 2))
        annotation = {'start_sec': 2, 'end_sec': 4, 'reviewer': 'Synthetic reviewer', 'observed_fact': 'Synthetic observation', 'type': 'Synthetic action', 'visibility': 'clear', 'confirmed': True, 'origin': 'human', 'rule_id': None, 'rule_status': 'unresolved', 'rule_applicability_confirmed': False}
        self.assertTrue(validate_annotation(annotation, 10)['confirmed'])


if __name__ == '__main__':
    unittest.main()
