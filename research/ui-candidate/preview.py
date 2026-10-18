"""Loopback UI and fixed-destination, endpoint-allowlisted streaming API bridge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import unquote, urlsplit
import json
import os
import re
import socket
import threading
import time

ROOT = Path(__file__).resolve().parent
ASSETS = {name: kind for name, kind in (
    ('index.html', 'text/html; charset=utf-8'), ('styles.css', 'text/css; charset=utf-8'),
    ('app.mjs', 'text/javascript; charset=utf-8'), ('demo.mjs', 'text/javascript; charset=utf-8'),
    ('api.mjs', 'text/javascript; charset=utf-8'), ('real.mjs', 'text/javascript; charset=utf-8'),
    ('real-view.mjs', 'text/javascript; charset=utf-8'), ('scene.svg', 'image/svg+xml'), ('rule-checklist.mjs', 'text/javascript; charset=utf-8'),
    ('rule-catalog.json', 'application/json; charset=utf-8'))}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'none'; media-src blob: 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CHUNK = 64 * 1024
UPLOAD_LIMIT = 101 * 1024 * 1024
JSON_LIMIT = 16384
RESPONSE_LIMIT = 128 * 1024 * 1024
MEDIA_LIMIT = 512 * 1024 * 1024
TOTAL_TIMEOUT = 280
CLIENT_HEADER = 'ui-candidate-v1'
JOB = r'[a-f0-9]{32}'


def api_route(method, path):
    if '?' in path or '#' in path or '%' in path or not path.startswith('/bridge/'):
        return None
    target = path[len('/bridge'):]
    if method == 'GET' and target in ('/health/ready', '/api/v1/rules'):
        return target
    if method == 'POST' and target == '/api/v1/analyze':
        return target
    if re.fullmatch(r'/api/v1/jobs/' + JOB + r'(?:/(?:review|video|annotations|report\.pdf|passport))?', target):
        if method == 'GET' or (method == 'HEAD' and target.endswith('/video')) or (method == 'POST' and target.endswith('/annotations')):
            return target
    return None


def resolve_asset(target):
    try:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return None
        path = unquote(parsed.path)
    except ValueError:
        return None
    name = 'index.html' if path == '/' else path[1:] if path.startswith('/') else ''
    if name not in ASSETS:
        return None
    candidate = ROOT / name
    if candidate.is_symlink() or candidate.resolve().parent != ROOT or not candidate.is_file():
        return None
    return candidate, ASSETS[name]


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    # Fixed production destination; tests explicitly subclass on ephemeral loopback.
    backend_address = ('127.0.0.1', 8000)
    upstream_timeout = 70  # readiness can take 60 seconds
    slots = threading.BoundedSemaphore(4)

    def handle_error(self, *_args):
        pass  # Never print a traceback containing request values.


class Handler(BaseHTTPRequestHandler):
    server_version, sys_version = 'LocalAuditUI', ''

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def _headers(self, content_type, length=None):
        self.send_header('Content-Type', content_type)
        if length is not None:
            self.send_header('Content-Length', str(length))
        for name, value in (('Content-Security-Policy', CSP), ('X-Content-Type-Options', 'nosniff'),
                            ('Referrer-Policy', 'no-referrer'), ('Cache-Control', 'no-store'),
                            ('X-Frame-Options', 'DENY'), ('Connection', 'close')):
            self.send_header(name, value)
        self.close_connection = True

    def _error(self, status, ready=False):
        body = json.dumps({'ready': False} if ready else {'detail': 'Request unavailable.'}).encode()
        self.send_response(status)
        self._headers('application/json', len(body))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _allowed_hosts(self):
        port = self.server.server_address[1]
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        name = os.environ.get('CODESPACE_NAME', '')
        domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev')
        if re.fullmatch(r'[a-z0-9-]+', name) and re.fullmatch(r'[a-z0-9.-]+', domain):
            allowed.add(f'{name}-{port}.{domain}')
        return allowed

    def _host_allowed(self):
        return len(self.headers.get_all('Host', [])) == 1 and self.headers.get('Host', '') in self._allowed_hosts()

    def _proxy(self, target):
        if not self._host_allowed() or self.headers.get('X-Cashframe-Client') != CLIENT_HEADER:
            self._error(403); return
        if self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin':
            self._error(403); return
        origin = self.headers.get('Origin')
        if origin and origin not in {scheme + host for host in self._allowed_hosts() for scheme in ('http://', 'https://')}:
            self._error(403); return
        key = self.headers.get('X-API-Key', '')
        if target != '/health/ready' and (len(self.headers.get_all('X-API-Key', [])) != 1 or
                not key.strip() or len(key) > 4096 or not all(32 <= ord(c) < 127 for c in key)):
            self._error(401); return
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) > 1:
            self._error(400); return
        raw_length = self.headers.get('Content-Length', '0')
        if not re.fullmatch(r'\d{1,12}', raw_length):
            self._error(400); return
        length = int(raw_length)
        content_type = self.headers.get('Content-Type', '')
        if self.command == 'POST':
            limit = UPLOAD_LIMIT if target == '/api/v1/analyze' else JSON_LIMIT
            expected = 'multipart/form-data;' if target == '/api/v1/analyze' else 'application/json'
            if not length:
                self._error(411); return
            if length > limit:
                self._error(413); return
            if (not content_type.lower().startswith(expected) or len(content_type) > 512 or
                    (target.endswith('/annotations') and content_type.split(';', 1)[0].lower() != 'application/json') or
                    (target == '/api/v1/analyze' and not re.search(r'boundary=[^;]+', content_type, re.IGNORECASE))):
                self._error(415); return
        elif length:
            self._error(400); return
        if not self.server.slots.acquire(blocking=False):
            self._error(429); return
        upstream = None
        started, headers_sent = time.monotonic(), False
        try:
            upstream = HTTPConnection(*self.server.backend_address, timeout=self.server.upstream_timeout)
            upstream.putrequest(self.command, target, skip_accept_encoding=True)
            if target != '/health/ready':
                upstream.putheader('X-API-Key', key)
            upstream.putheader('Connection', 'close')
            if self.command == 'POST':
                upstream.putheader('Content-Type', content_type)
                upstream.putheader('Content-Length', str(length))
            for name in ('Range', 'If-Range'):
                value = self.headers.get(name)
                if value and target.endswith('/video'):
                    if len(value) > 256 or (name == 'Range' and not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', value)):
                        self._error(400); return
                    upstream.putheader(name, value)
            upstream.endheaders()
            remaining = length
            while remaining:
                if time.monotonic() - started > TOTAL_TIMEOUT:
                    raise TimeoutError()
                part = self.rfile.read1(min(CHUNK, remaining))
                if not part:
                    raise ConnectionError()
                upstream.send(part)
                remaining -= len(part)
            response = upstream.getresponse()
            if response.status >= 400:
                self.send_response(response.status)
                body = json.dumps({'ready': False} if target == '/health/ready' else {'detail': 'Backend request unavailable.'}).encode()
                self._headers('application/json', len(body))
                retry = response.getheader('Retry-After', '')
                if re.fullmatch(r'\d{1,5}', retry):
                    self.send_header('Retry-After', retry)
                self.end_headers(); headers_sent = True
                if self.command != 'HEAD':
                    self.wfile.write(body)
                return
            if 300 <= response.status < 400:
                self._error(502); return  # No redirects, especially to external URLs.
            max_response = MEDIA_LIMIT if target.endswith('/video') else RESPONSE_LIMIT
            advertised = response.getheader('Content-Length')
            if advertised and (not advertised.isdigit() or int(advertised) > max_response):
                self._error(502); return
            self.send_response(response.status)
            self._headers(response.getheader('Content-Type', 'application/octet-stream'), int(advertised) if advertised else None)
            for name in ('Content-Disposition', 'X-Report-SHA256', 'X-Passport-SHA256', 'Accept-Ranges',
                         'Content-Range', 'ETag', 'Last-Modified'):
                value = response.getheader(name)
                if value and len(value) <= 1024 and '\r' not in value and '\n' not in value:
                    self.send_header(name, value)
            self.end_headers(); headers_sent = True
            if self.command == 'HEAD':
                return
            total = 0
            while True:
                if time.monotonic() - started > TOTAL_TIMEOUT:
                    raise TimeoutError()
                part = response.read1(min(CHUNK, max_response - total + 1))
                if not part:
                    break
                total += len(part)
                if total > max_response:
                    raise ConnectionError()
                self.wfile.write(part)
        except (OSError, HTTPException, ValueError) as exc:
            if not headers_sent:
                self._error(504 if isinstance(exc, (socket.timeout, TimeoutError)) else 502)
        finally:
            if upstream:
                upstream.close()
            self.server.slots.release()

    def _dispatch(self):
        target = api_route(self.command, self.path)
        if target:
            self._proxy(target); return
        if self.path.startswith('/bridge/'):
            self._error(404); return
        if self.command not in ('GET', 'HEAD'):
            self._error(405); return
        asset = resolve_asset(self.path)
        if not asset:
            self._error(404); return
        try:
            body = asset[0].read_bytes()
        except OSError:
            self._error(404); return
        self.send_response(200); self._headers(asset[1], len(body)); self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = _dispatch

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    try:
        with PreviewServer(('127.0.0.1', 8001), Handler) as server:
            print('Audit UI: http://127.0.0.1:8001 · loopback only · Demo default · fixed local API bridge', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print('Audit UI stopped.', flush=True)
    except OSError:
        raise SystemExit('Cannot bind localhost:8001. No other service was stopped.')
