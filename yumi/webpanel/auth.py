"""The panel's little lock: one random token, signed sessions, CSRF and a dumb limiter.

The token and the signing secret live in ``~/.config/yumi/webpanel.json`` with mode 0600.
The browser gets an HttpOnly session cookie (``yumi_web``) whose value is an expiry plus
an HMAC, and a readable ``yumi_csrf`` cookie; every mutation must echo that value in the
``X-Yumi-CSRF`` header.  Losing a cookie means asking for the token again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

AUTH_COOKIE = 'yumi_web'
CSRF_COOKIE = 'yumi_csrf'
VERSION = 'v1'
TOKEN_BYTES = 32


def _b64(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


class Auth:
    def __init__(self, path, session_hours=168):
        self.path = Path(path)
        self.session_hours = max(1, int(session_hours or 168))
        self.lock = threading.Lock()
        self.data = self._load()
        self.fails = {}          # ip -> [timestamps]

    # ------------------------------------------------------------------ storage
    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, ValueError):
            data = {}
        changed = False
        if not data.get('token') or len(str(data['token'])) < 24:
            data['token'] = _b64(secrets.token_bytes(TOKEN_BYTES))
            changed = True
        if not data.get('secret') or len(str(data['secret'])) < 24:
            data['secret'] = _b64(secrets.token_bytes(TOKEN_BYTES))
            changed = True
        data.setdefault('created', int(time.time()))
        if changed:
            self._save(data)
        else:
            try:                     # a file from an older, looser umask
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return data

    def _save(self, data=None):
        data = data or self.data
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=1))
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.path)
        except OSError:
            pass

    # ------------------------------------------------------------------ tokens
    @property
    def token(self):
        return str(self.data.get('token') or '')

    def set_session_hours(self, hours):
        self.session_hours = max(1, int(hours or 168))

    def rotate(self):
        """A new access link without touching live sessions: the session cookie is
        signed with ``secret``, the link is the token.  The old link dies, the
        browser that asked keeps working."""
        with self.lock:
            self.data['token'] = _b64(secrets.token_bytes(TOKEN_BYTES))
            self.data['rotated'] = int(time.time())
            self._save()
        return self.token

    def check_token(self, given):
        return bool(given) and hmac.compare_digest(str(given), self.token)

    # ------------------------------------------------------------------ sessions
    def _sign(self, payload: str) -> str:
        secret = str(self.data.get('secret') or '').encode()
        return hmac.new(secret, f'{VERSION}|{payload}'.encode(), hashlib.sha256).hexdigest()

    def issue(self):
        """(session value, csrf value, expiry timestamp)"""
        exp = int(time.time()) + self.session_hours * 3600
        payload = str(exp)
        session = f'{payload}.{self._sign(payload)}'
        csrf = _b64(hmac.new(str(self.data.get('secret')).encode(),
                             f'csrf|{payload}'.encode(), hashlib.sha256).digest())[:32]
        return session, csrf, exp

    def verify(self, cookie):
        try:
            payload, signature = str(cookie or '').split('.', 1)
            exp = int(payload)
        except (ValueError, AttributeError):
            return False
        if exp < int(time.time()):
            return False
        return hmac.compare_digest(signature, self._sign(payload))

    def csrf_for(self, cookie):
        try:
            payload = str(cookie or '').split('.', 1)[0]
            int(payload)
        except (ValueError, AttributeError):
            return ''
        return _b64(hmac.new(str(self.data.get('secret')).encode(),
                             f'csrf|{payload}'.encode(), hashlib.sha256).digest())[:32]

    # ------------------------------------------------------------------ failures
    def blocked(self, ip, limit=10, window=60.0):
        """True while this address has burned through its tries"""
        now = time.monotonic()
        tries = [t for t in self.fails.get(ip, []) if now - t < window]
        self.fails[ip] = tries
        return len(tries) >= limit

    def fail(self, ip):
        self.fails.setdefault(ip, []).append(time.monotonic())

    def success(self, ip):
        self.fails.pop(ip, None)

    def info(self):
        return {
            'token_set': bool(self.token),
            'created': self.data.get('created'),
            'rotated': self.data.get('rotated'),
            'session_hours': self.session_hours,
            'path': str(self.path),
        }
