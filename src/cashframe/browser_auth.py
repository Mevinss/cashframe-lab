"""Bounded, short-lived browser access; API keys never enter browser storage."""
import hashlib
import secrets
import threading
import time


COOKIE_NAME = 'cashframe_session'
SESSION_SECONDS = 8 * 60 * 60


class BrowserSessions:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def issue(self):
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._sessions = {key:expiry for key,expiry in self._sessions.items() if expiry > now}
            if len(self._sessions) >= 128:
                del self._sessions[min(self._sessions, key=self._sessions.get)]
            self._sessions[self._digest(token)] = now + SESSION_SECONDS
        return token

    def valid(self, token):
        if not isinstance(token,str) or not token or len(token)>128:
            return False
        with self._lock:
            return self._sessions.get(self._digest(token),0) > time.monotonic()

    def revoke(self, token):
        if token:
            with self._lock:
                self._sessions.pop(self._digest(token), None)
