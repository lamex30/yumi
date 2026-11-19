"""The HTTP server and its tiny API.

Only the standard library is used.  The live Yumi passes a bridge object (see
``yumi/ui/bridge.py``) whose methods run on the GTK thread; this module never imports GTK.
Every mutating call is written to a local audit log, and every response carries a
strict Content-Security-Policy so a stray string can never load a remote script.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import socket
import ssl  # noqa: F401  (kept so HTTPS can be added without touching the handler)
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config
from . import catalog
from .auth import AUTH_COOKIE, CSRF_COOKIE, Auth

WEBUI = Path(__file__).resolve().parent.parent / 'webui'
SPRITES = Path(config.SPRITES)
STATE_MARK = '__webpanel__'


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


class EventBus:
    """one queue per open event-stream; publishing never blocks"""

    def __init__(self):
        self.lock = threading.Lock()
        self.subs = set()

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def publish(self, event, data=None):
        payload = {'event': event, 'time': time.time(), 'data': data}
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass


class Panel:
    def __init__(self, settings, bridge, config_dir=None):
        self.settings = settings
        self.bridge = bridge
        self.config_dir = Path(config_dir or config.CONFIG_DIR)
        self.auth = Auth(self.config_dir / 'webpanel.json',
                         settings.get('web_panel_session_hours', 168))
        self.bus = EventBus()
        self.server = None
        self.thread = None
        self.error = None
        self.started_at = None
        self.audit_lock = threading.Lock()
        self.audit_path = Path(config.STATE_DIR) / 'web-audit.log'

    # ------------------------------------------------------------------ lifecycle
    @property
    def running(self):
        return self.server is not None

    @property
    def host(self):
        return str(self.settings.get('web_panel_host') or '127.0.0.1')

    @property
    def port(self):
        try:
            return int(self.settings.get('web_panel_port') or 8765)
        except (TypeError, ValueError):
            return 8765

    def start(self):
        if self.server:
            return True
        self.auth.set_session_hours(self.settings.get('web_panel_session_hours', 168))
        handler = _make_handler(self)
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            self.error = str(exc)
            self.server = None
            return False
        self.server.daemon_threads = True
        self.started_at = time.time()
        self.error = None
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.4}, daemon=True)
        self.thread.start()
        self.audit('system', 'start', f'{self.host}:{self.port}')
        self.bus.publish('server', self.info())
        return True

    def stop(self):
        srv, self.server = self.server, None
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass
        self.thread = None
        self.audit('system', 'stop', '')
        self.bus.publish('server', {'running': False})

    def url(self, with_token=False):
        host = self.host
        if host in ('0.0.0.0', '::'):
            host = '127.0.0.1'
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        base = f'http://{host}:{self.port}/'
        if with_token:
            base += '?t=' + urllib.parse.quote(self.auth.token)
        return base

    def info(self):
        return {
            'running': self.running,
            'host': self.host,
            'port': self.port,
            'url': self.url(False),
            'started_at': self.started_at,
            'error': self.error,
            'session_hours': self.auth.session_hours,
            'token_set': bool(self.auth.token),
        }

    def rotate(self):
        token = self.auth.rotate()
        self.audit('security', 'rotate', 'the access token was replaced')
        return token

    # ------------------------------------------------------------------ audit
    def audit(self, ip, action, detail=''):
        line = json.dumps({'ts': _now(), 'epoch': time.time(), 'ip': ip,
                           'action': action, 'detail': str(detail)[:500]},
                          ensure_ascii=False)
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_lock:
                with self.audit_path.open('a', encoding='utf-8') as fh:
                    fh.write(line + '\n')
                try:
                    os.chmod(self.audit_path, 0o600)
                except OSError:
                    pass
        except OSError:
            pass

    def audit_tail(self, limit=200):
        try:
            lines = self.audit_path.read_text(errors='replace').splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    # ------------------------------------------------------------------ helpers
    def publish(self, event, data=None):
        self.bus.publish(event, data)

    def status(self):
        data = self.bridge.snapshot()
        data['panel'] = self.info()
        data['security'] = {'remote': self.host not in ('127.0.0.1', '::1'),
                            'session_hours': self.auth.session_hours}
        return data


# --------------------------------------------------------------------- the handler
def _make_handler(panel):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'YumiPanel/1.0'
        protocol_version = 'HTTP/1.1'

        # ---------------------------------------------------------------- plumbing
        def log_message(self, fmt, *args):
            # quiet: the panel has its own audit and Yumi writes the important bits
            return

        def _ip(self):
            return self.client_address[0] if self.client_address else '?'

        def _cookies(self):
            out = {}
            for part in (self.headers.get('Cookie') or '').split(';'):
                if '=' in part:
                    k, v = part.split('=', 1)
                    out[k.strip()] = v.strip()
            return out

        def _authed(self):
            return panel.auth.verify(self._cookies().get(AUTH_COOKIE))

        def _csrf_ok(self):
            if not self._authed():
                return False
            cookies = self._cookies()
            given = self.headers.get('X-Yumi-CSRF') or ''
            want = panel.auth.csrf_for(cookies.get(AUTH_COOKIE))
            return bool(want) and _equal(given, want) and _equal(cookies.get(CSRF_COOKIE, ''), want)

        def _origin_ok(self):
            origin = self.headers.get('Origin')
            if not origin:
                return True
            host = self.headers.get('Host') or ''
            try:
                parsed = urllib.parse.urlparse(origin)
            except ValueError:
                return False
            return bool(parsed.netloc) and parsed.netloc == host

        def _host_ok(self):
            raw = (self.headers.get('Host') or '').strip()
            if raw.startswith('['):
                host = raw[1:raw.find(']')] if ']' in raw else raw.strip('[]')
            else:
                host = raw.split(':')[0]
            host = host.lower()
            bind = panel.host
            if bind in ('127.0.0.1', '::1'):
                return host in ('127.0.0.1', 'localhost', '::1')
            if bind in ('0.0.0.0', '::'):
                return bool(host)  # the token is the wall when it is exposed on purpose
            return host in (bind.lower(), 'localhost') or not host

        def _send(self, code, body=b'', ctype='application/octet-stream', extra=None):
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; img-src 'self' data: blob:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                             "form-action 'self'")
            for key, value in (extra or {}).items():
                if isinstance(value, (list, tuple)):
                    for item in value:
                        self.send_header(key, item)
                else:
                    self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _json(self, code, obj, extra=None):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self._send(code, body, 'application/json; charset=utf-8', extra)

        def _err(self, code, message, extra=None):
            self._json(code, {'ok': False, 'error': str(message)}, extra)

        def _body(self, limit=2 * 1024 * 1024):
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            if length > limit:
                raise ValueError('request body is too large')
            raw = self.rfile.read(length) if length else b''
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                raise ValueError('the body must be JSON')
            return data if isinstance(data, dict) else {'value': data}

        # ---------------------------------------------------------------- routing
        def do_HEAD(self):
            self.do_GET()

        def do_OPTIONS(self):
            self._send(405, b'', 'text/plain')

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def do_PUT(self):
            self._route('PUT')

        def do_PATCH(self):
            self._route('PATCH')

        def do_DELETE(self):
            self._route('DELETE')

        def _route(self, method):
            if not self._host_ok():
                return self._err(421, 'wrong Host header')
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)

            # public, unauthenticated surface
            if path == '/api/session' and method == 'GET':
                authed = self._authed()
                data = {'ok': True, 'authed': authed,
                        'panel': panel.info(),
                        'version': __import__('yumi').__version__,
                        'remote': panel.host not in ('127.0.0.1', '::1')}
                if authed:
                    data['csrf'] = panel.auth.csrf_for(self._cookies().get(AUTH_COOKIE))
                return self._json(200, data)
            if path == '/api/login' and method == 'POST':
                return self._login()
            if path.startswith('/static/') or path in ('/', '/index.html'):
                return self._static(path)
            if path.startswith('/art/'):
                return self._art(path[5:])
            if path == '/favicon.ico':
                return self._art('icon')

            # everything below needs a session
            if not self._authed():
                return self._err(401, 'the panel asks for its token')
            if method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                if not self._origin_ok():
                    return self._err(403, 'cross-origin request refused')
                if not self._csrf_ok():
                    return self._err(403, 'missing or wrong CSRF token')

            routes = {
                ('POST', '/api/logout'): self._logout,
                ('GET', '/api/status'): self._status,
                ('GET', '/api/schema'): self._schema,
                ('GET', '/api/settings'): self._settings_get,
                ('PATCH', '/api/settings'): self._settings_patch,
                ('POST', '/api/settings/reset'): self._settings_reset,
                ('GET', '/api/configs'): self._configs,
                ('GET', '/api/actions'): self._actions_get,
                ('PUT', '/api/actions'): self._actions_put,
                ('GET', '/api/files/recent'): self._recent,
                ('GET', '/api/files/shelf'): self._shelf,
                ('POST', '/api/files/shelf'): self._shelf_change,
                ('POST', '/api/files/open'): self._open,
                ('POST', '/api/files/job'): self._job,
                ('POST', '/api/files/jobs/cancel'): self._jobs_cancel,
                ('GET', '/api/timer'): self._timer_get,
                ('POST', '/api/timer'): self._timer_post,
                ('POST', '/api/ask'): self._ask,
                ('GET', '/api/chat/history'): self._chat_history,
                ('POST', '/api/chat'): self._chat,
                ('DELETE', '/api/chat/history'): self._chat_clear,
                ('GET', '/api/memory'): self._memory,
                ('POST', '/api/memory/clear'): self._memory_clear,
                ('GET', '/api/notes'): self._notes,
                ('POST', '/api/notes'): self._note_add,
                ('POST', '/api/notes/clear'): self._notes_clear,
                ('GET', '/api/music'): self._music_status,
                ('GET', '/api/music/search'): self._music_search,
                ('POST', '/api/music/play'): self._music_play,
                ('POST', '/api/music/control'): self._music_control,
                ('POST', '/api/voice/listen'): self._voice_listen,
                ('POST', '/api/voice/stop'): self._voice_stop,
                ('POST', '/api/voice/speak'): self._voice_speak,
                ('GET', '/api/voice/engines'): self._voice_engines,
                ('GET', '/api/voice/mics'): self._voice_mics,
                ('POST', '/api/voice/test-wake'): self._voice_test_wake,
                ('POST', '/api/voice/test'): self._voice_test,
                ('GET', '/api/security'): self._security,
                ('POST', '/api/security/rotate'): self._security_rotate,
                ('POST', '/api/security/link'): self._security_link,
                ('GET', '/api/audit'): self._audit,
                ('GET', '/api/logs'): self._logs,
                ('GET', '/api/events'): self._events,
                ('GET', '/api/tools/time'): self._tool_time,
                ('GET', '/api/tools/weather'): self._tool_weather,
                ('GET', '/api/tools/joke'): self._tool_joke,
                ('GET', '/api/tools/params'): self._tool_params,
            }
            fn = routes.get((method, path))
            if fn:
                try:
                    return fn(query)
                except ValueError as exc:
                    return self._err(400, exc)
                except TimeoutError:
                    return self._err(504, 'Yumi did not answer in time')
                except Exception as exc:  # noqa: BLE001 - the bridge is a wide surface
                    traceback.print_exc()
                    return self._err(500, exc)

            # parameterised routes
            try:
                m = re.fullmatch(r'/api/config/([a-z]+)', path)
                if m and method == 'GET':
                    return self._config_get(m.group(1))
                if m and method == 'PUT':
                    return self._config_put(m.group(1))
                m = re.fullmatch(r'/api/state/([a-z_]+)', path)
                if m and method == 'POST':
                    return self._state(m.group(1))
                m = re.fullmatch(r'/api/actions/(\d+)/run', path)
                if m and method == 'POST':
                    return self._action_run(int(m.group(1)))
                m = re.fullmatch(r'/api/notes/(\d+)', path)
                if m and method == 'GET':
                    return self._note_get(int(m.group(1)))
                if m and method in ('PATCH', 'PUT'):
                    return self._note_update(int(m.group(1)))
                if m and method == 'DELETE':
                    return self._note_delete(int(m.group(1)))
                if path == '/api/upload' and method == 'POST':
                    return self._upload(query)
                if path == '/api/file/preview' and method == 'GET':
                    return self._preview(query)
            except ValueError as exc:
                return self._err(400, exc)
            except TimeoutError:
                return self._err(504, 'Yumi did not answer in time')
            except Exception as exc:  # noqa: BLE001 - the bridge is a wide surface
                traceback.print_exc()
                return self._err(500, exc)
            return self._err(404, 'no such route')

        # ---------------------------------------------------------------- auth
        def _login(self):
            ip = self._ip()
            if panel.auth.blocked(ip):
                return self._err(429, 'too many tries; wait a minute', {'Retry-After': '60'})
            try:
                body = self._body(64 * 1024)
            except ValueError as exc:
                return self._err(400, exc)
            given = str(body.get('token') or '').strip()
            if given.startswith('http'):
                parsed = urllib.parse.urlsplit(given)
                given = urllib.parse.parse_qs(parsed.query).get('t', [''])[0]
            if not panel.auth.check_token(given):
                panel.auth.fail(ip)
                panel.audit(ip, 'login', 'wrong token')
                return self._err(403, 'wrong token')
            panel.auth.success(ip)
            session, csrf, exp = panel.auth.issue()
            panel.audit(ip, 'login', 'ok')
            age = max(60, exp - int(time.time()))
            cookies = [
                f'{AUTH_COOKIE}={session}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict',
                f'{CSRF_COOKIE}={csrf}; Path=/; Max-Age={age}; SameSite=Strict',
            ]
            return self._json(200, {'ok': True, 'csrf': csrf, 'expires': exp,
                                    'panel': panel.info()},
                              {'Set-Cookie': cookies})

        def _logout(self, _q):
            panel.audit(self._ip(), 'logout', '')
            return self._json(200, {'ok': True}, {
                'Set-Cookie': [f'{AUTH_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict',
                               f'{CSRF_COOKIE}=; Path=/; Max-Age=0; SameSite=Strict']})

        # ---------------------------------------------------------------- status
        def _status(self, _q):
            return self._json(200, {'ok': True, **panel.status()})

        def _schema(self, _q):
            return self._json(200, {'ok': True, **catalog.schema()})

        def _settings_get(self, _q):
            return self._json(200, {'ok': True, 'settings': catalog.redact(panel.settings),
                                    'known': list(config.DEFAULT_SETTINGS)})

        def _settings_patch(self, _q):
            body = self._body()
            patch = body.get('patch', body)
            clean = catalog.validate_settings_patch(patch)
            before = {k: panel.settings.get(k) for k in clean}
            result = panel.bridge.apply_settings(clean)
            panel.audit(self._ip(), 'settings', ', '.join(clean))
            panel.publish('settings', {'changed': clean, 'settings': catalog.redact(panel.settings)})
            return self._json(200, {'ok': True, 'changed': clean, 'before': before,
                                    'result': result, 'settings': catalog.redact(panel.settings)})

        def _settings_reset(self, _q):
            body = self._body()
            key = str(body.get('key') or '')
            if key:
                if key not in config.DEFAULT_SETTINGS:
                    raise ValueError('unknown setting')
                clean = {key: config.DEFAULT_SETTINGS[key]}
            else:
                if body.get('all') is not True:
                    raise ValueError('refusing to reset everything without "all": true')
                clean = dict(config.DEFAULT_SETTINGS)
            panel.bridge.apply_settings(clean)
            panel.audit(self._ip(), 'settings-reset', key or 'all')
            panel.publish('settings', {'changed': clean,
                                       'settings': catalog.redact(panel.settings)})
            return self._json(200, {'ok': True, 'reset': list(clean)})

        # ---------------------------------------------------------------- configs
        def _configs(self, _q):
            return self._json(200, {'ok': True, 'files': panel.bridge.configs()})

        def _config_get(self, name):
            if name not in catalog.CONFIG_FILES:
                raise ValueError('unknown config file')
            return self._json(200, {'ok': True, **panel.bridge.config_get(name)})

        def _config_put(self, name):
            if name not in catalog.CONFIG_FILES:
                raise ValueError('unknown config file')
            body = self._body(4 * 1024 * 1024)
            result = panel.bridge.config_save(name, body.get('data', body.get('text')))
            panel.audit(self._ip(), 'config-save', name)
            panel.publish('settings', {'config': name})
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- actions
        def _actions_get(self, _q):
            return self._json(200, {'ok': True, **panel.bridge.actions_get()})

        def _actions_put(self, _q):
            body = self._body()
            actions = body.get('actions')
            if not isinstance(actions, list):
                raise ValueError('actions must be a list')
            result = panel.bridge.actions_save(actions)
            panel.audit(self._ip(), 'actions-save', f'{len(actions)} buttons')
            panel.publish('actions', result)
            return self._json(200, {'ok': True, **result})

        def _action_run(self, index):
            result = panel.bridge.action_run(index)
            panel.audit(self._ip(), 'action-run', str(index))
            panel.publish('actions', {'ran': index})
            return self._json(200, {'ok': True, **result})

        def _state(self, name):
            body = self._body()
            result = panel.bridge.state_action(name, body)
            panel.audit(self._ip(), 'state', name)
            panel.publish('status', panel.bridge.snapshot())
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- files
        def _recent(self, _q):
            return self._json(200, {'ok': True, 'items': panel.bridge.files_recent()})

        def _shelf(self, _q):
            return self._json(200, {'ok': True, 'items': panel.bridge.files_shelf()})

        def _shelf_change(self, _q):
            body = self._body()
            paths = body.get('paths') or ([body['path']] if body.get('path') else [])
            if not isinstance(paths, list):
                raise ValueError('paths must be a list')
            add = bool(body.get('add', True))
            result = panel.bridge.shelf_change([str(p) for p in paths], add)
            panel.audit(self._ip(), 'shelf', ('add ' if add else 'remove ') + str(len(paths)))
            panel.publish('files', {'shelf': True})
            return self._json(200, {'ok': True, **result})

        def _open(self, _q):
            body = self._body()
            result = panel.bridge.files_open(str(body.get('path') or ''))
            panel.audit(self._ip(), 'open', body.get('path'))
            return self._json(200, {'ok': True, **result})

        def _job(self, _q):
            body = self._body()
            op = str(body.get('op') or '')
            path = str(body.get('path') or '')
            arg = str(body.get('arg') or '')
            result = panel.bridge.job_add(op, path, arg)
            panel.audit(self._ip(), 'job', f'{op} {path} {arg}')
            panel.publish('jobs', panel.bridge.snapshot().get('jobs'))
            return self._json(200, {'ok': True, **result})

        def _jobs_cancel(self, _q):
            panel.bridge.jobs_cancel()
            panel.audit(self._ip(), 'jobs-cancel', '')
            return self._json(200, {'ok': True})

        def _upload(self, query):
            name = Path(str((query.get('name') or ['file'])[0])).name[:180] or 'file'
            name = re.sub(r'[^\w.\- ()\[\]]+', '_', name)
            length = int(self.headers.get('Content-Length') or 0)
            if length > 1024 * 1024 * 1024:
                return self._err(413, 'file is larger than 1 GB')
            dest = Path(config.CACHE_DIR) / 'uploads'
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / f'{int(time.time())}-{name}'
            left = length
            with target.open('wb') as fh:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            panel.audit(self._ip(), 'upload', f'{name} ({length} bytes)')
            return self._json(200, {'ok': True, 'path': str(target), 'name': name,
                                    'size': target.stat().st_size if target.exists() else 0})

        def _preview(self, query):
            raw = (query.get('path') or [''])[0]
            allowed = getattr(panel.bridge, 'file_allowed', None)
            if allowed is not None and not allowed(raw):
                return self._err(403, 'this file is not one the panel knows')
            try:
                path = Path(raw).expanduser().resolve()
            except (OSError, RuntimeError):
                return self._err(400, 'bad path')
            if not path.is_file() or path.stat().st_size > 24 * 1024 * 1024:
                return self._err(404, 'not a picture')
            mime = mimetypes.guess_type(str(path))[0] or ''
            if not mime.startswith('image/'):
                return self._err(415, 'not an image')
            try:
                data = path.read_bytes()
            except OSError as exc:
                return self._err(404, exc)
            return self._send(200, data, mime, {'Cache-Control': 'private, max-age=60'})

        # ---------------------------------------------------------------- timer
        def _timer_get(self, _q):
            return self._json(200, {'ok': True, 'timer': panel.bridge.timer_get()})

        def _timer_post(self, _q):
            body = self._body()
            result = panel.bridge.timer(str(body.get('action') or 'set'),
                                        body.get('seconds'))
            panel.audit(self._ip(), 'timer', f"{body.get('action')} {body.get('seconds')}")
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- chat
        def _ask(self, _q):
            body = self._body()
            text = str(body.get('text') or '').strip()
            if not text:
                raise ValueError('a phrase is needed')
            result = panel.bridge.ask(text, speak=bool(body.get('speak', True)))
            panel.audit(self._ip(), 'ask', text[:160])
            return self._json(200, {'ok': True, **result})

        def _chat(self, _q):
            body = self._body()
            text = str(body.get('text') or '').strip()
            if not text:
                raise ValueError('a message is needed')
            result = panel.bridge.chat(text)
            panel.audit(self._ip(), 'chat', text[:160])
            panel.publish('chat', {'reply': result.get('reply', '')})
            return self._json(200, {'ok': True, **result})

        def _chat_history(self, _q):
            return self._json(200, {'ok': True, 'messages': panel.bridge.chat_history()})

        def _chat_clear(self, _q):
            panel.bridge.chat_clear()
            panel.audit(self._ip(), 'chat-clear', '')
            return self._json(200, {'ok': True})

        # ---------------------------------------------------------------- memory
        def _memory(self, _q):
            q = str((_q.get('q') or [''])[0]).strip()
            try:
                limit = min(200, max(1, int((_q.get('limit') or ['50'])[0])))
            except ValueError:
                limit = 50
            data = (panel.bridge.memory_search(q, limit) if q
                    else panel.bridge.memory_recent(limit))
            return self._json(200, {'ok': True, 'query': q, 'items': data})

        def _memory_clear(self, _q):
            body = self._body()
            tag = str(body.get('tag') or '')
            result = panel.bridge.memory_clear(tag)
            panel.audit(self._ip(), 'memory-clear', tag or 'all web lines')
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- notes
        def _notes(self, query):
            q = str((query.get('q') or [''])[0]).strip()
            try:
                limit = min(500, max(1, int((query.get('limit') or ['50'])[0])))
            except ValueError:
                limit = 50
            items = (panel.bridge.notes_search(q, limit) if q
                     else panel.bridge.notes_list(limit))
            return self._json(200, {'ok': True, 'query': q, 'items': items})

        def _note_get(self, note_id):
            note = panel.bridge.notes_get(note_id)
            if not note:
                raise ValueError('there is no such note')
            return self._json(200, {'ok': True, 'note': note})

        def _note_add(self, _q):
            body = self._body()
            text = str(body.get('text') or '').strip()
            if not text:
                raise ValueError('a note needs some text')
            note = panel.bridge.notes_add(text, str(body.get('title') or '') or None)
            panel.audit(self._ip(), 'note-add', f"#{note.get('id')} {text[:100]}")
            panel.publish('notes', {'changed': note.get('id'), 'action': 'add', 'note': note})
            return self._json(200, {'ok': True, 'note': note})

        def _note_update(self, note_id):
            body = self._body()
            text = str(body.get('text') or '').strip()
            if not text:
                raise ValueError('a note needs some text')
            note = panel.bridge.notes_update(note_id, text, str(body.get('title') or '') or None)
            if not note:
                raise ValueError('there is no such note')
            panel.audit(self._ip(), 'note-update', f'#{note_id} {text[:100]}')
            panel.publish('notes', {'changed': note_id, 'action': 'update', 'note': note})
            return self._json(200, {'ok': True, 'note': note})

        def _note_delete(self, note_id):
            result = panel.bridge.notes_delete(note_id)
            panel.audit(self._ip(), 'note-delete', f'#{note_id}')
            panel.publish('notes', {'changed': note_id, 'action': 'delete'})
            return self._json(200, {'ok': True, **result})

        def _notes_clear(self, _q):
            body = self._body()
            if body.get('all') is not True:
                raise ValueError('refusing to clear the notes without "all": true')
            result = panel.bridge.notes_clear()
            panel.audit(self._ip(), 'notes-clear', str(result.get('deleted', '')))
            panel.publish('notes', {'action': 'clear', **result})
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- music
        def _music_status(self, _q):
            return self._json(200, {'ok': True, **panel.bridge.music_status()})

        def _music_search(self, _q):
            kind = str((_q.get('kind') or ['song'])[0])
            text = str((_q.get('q') or [''])[0])
            return self._json(200, {'ok': True, **panel.bridge.music_search(kind, text)})

        def _music_play(self, _q):
            body = self._body()
            result = panel.bridge.music_play(str(body.get('kind') or 'song'),
                                             str(body.get('query') or ''))
            panel.audit(self._ip(), 'music-play', f"{body.get('kind')} {body.get('query')}")
            return self._json(200, {'ok': True, **result})

        def _music_control(self, _q):
            body = self._body()
            result = panel.bridge.music_control(str(body.get('cmd') or ''))
            panel.audit(self._ip(), 'music', body.get('cmd'))
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- voice
        def _voice_listen(self, _q):
            body = self._body()
            secs = float(body.get('seconds') or 8)
            engine = str(body.get('engine') or '') or None
            result = panel.bridge.voice_listen(secs, engine)
            panel.audit(self._ip(), 'voice-listen', f'{secs}s {engine or "settings"}')
            return self._json(200, {'ok': True, **result})

        def _voice_stop(self, _q):
            panel.bridge.voice_stop()
            return self._json(200, {'ok': True})

        def _voice_speak(self, _q):
            body = self._body()
            text = str(body.get('text') or '').strip()
            if not text:
                raise ValueError('text is needed')
            result = panel.bridge.voice_speak(text)
            panel.audit(self._ip(), 'voice-speak', text[:120])
            return self._json(200, {'ok': True, **result})

        def _voice_engines(self, _q):
            return self._json(200, {'ok': True, **panel.bridge.voice_engines()})

        def _voice_mics(self, _q):
            # a fresh look at the sound system, for the microphone picker
            return self._json(200, {'ok': True, **panel.bridge.voice_mics()})

        def _voice_test_wake(self, _q):
            body = self._body()
            secs = float(body.get('seconds') or 8)
            ear = str(body.get('ear') or '') or None
            result = panel.bridge.voice_test_wake(secs, ear)
            panel.audit(self._ip(), 'voice-test-wake', f'{secs}s {ear or "settings"}')
            return self._json(200, {'ok': True, **result})

        def _voice_test(self, _q):
            body = self._body()
            text = str(body.get('text') or '').strip() or None
            lang = str(body.get('lang') or '') or None
            result = panel.bridge.voice_test(text, lang)
            panel.audit(self._ip(), 'voice-test', (text or 'sample')[:120])
            return self._json(200, {'ok': True, **result})

        # ---------------------------------------------------------------- security
        def _security(self, _q):
            return self._json(200, {'ok': True,
                                    'panel': panel.info(),
                                    'auth': panel.auth.info(),
                                    'audit': panel.audit_tail(60),
                                    'host': panel.host,
                                    'session_hours': panel.auth.session_hours,
                                    'remote': panel.host not in ('127.0.0.1', '::1')})

        def _security_rotate(self, _q):
            token = panel.rotate()
            return self._json(200, {'ok': True, 'token': token, 'url': panel.url(True),
                                    'note': 'every other browser session still works; '
                                            'only the link changes'})

        def _security_link(self, _q):
            """hand the authenticated browser its own access link, on request"""
            panel.audit(self._ip(), 'link', 'the token link was shown')
            return self._json(200, {'ok': True, 'url': panel.url(True)})

        def _audit(self, _q):
            return self._json(200, {'ok': True, 'items': panel.audit_tail(200)})

        # ---------------------------------------------------------------- logs
        def _logs(self, _q):
            name = str((_q.get('name') or ['yumi'])[0])
            try:
                limit = min(2000, max(1, int((_q.get('lines') or ['300'])[0])))
            except ValueError:
                limit = 300
            return self._json(200, {'ok': True, **panel.bridge.logs(name, limit)})

        # ---------------------------------------------------------------- small tools
        def _tool_time(self, _q):
            return self._json(200, {'ok': True, **panel.bridge.tools_time()})

        def _tool_weather(self, _q):
            where = str((_q.get('where') or [''])[0])
            when = str((_q.get('when') or ['today'])[0])
            result = panel.bridge.tools_weather(where, when)
            panel.audit(self._ip(), 'weather', f'{where or "home"} {when}')
            return self._json(200, {'ok': True, **result})

        def _tool_joke(self, _q):
            result = panel.bridge.tools_joke()
            panel.audit(self._ip(), 'joke', '')
            return self._json(200, {'ok': True, **result})

        def _tool_params(self, _q):
            return self._json(200, {'ok': True, **panel.bridge.tools_params()})

        # ---------------------------------------------------------------- events
        def _events(self, _q):
            q = panel.bus.subscribe()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            hello = json.dumps({'event': 'hello', 'time': time.time(),
                                'data': panel.info()}, ensure_ascii=False)
            try:
                self.wfile.write(f'event: hello\ndata: {hello}\n\n'.encode())
                self.wfile.flush()
                while True:
                    try:
                        item = q.get(timeout=12)
                        data = json.dumps(item, ensure_ascii=False)
                        self.wfile.write(f'event: {item["event"]}\ndata: {data}\n\n'.encode())
                    except queue.Empty:
                        self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                panel.bus.unsubscribe(q)
            self.close_connection = True

        # ---------------------------------------------------------------- static
        def _static(self, path):
            if path in ('/', '/index.html'):
                root, rel = WEBUI, 'index.html'
            else:
                root, rel = WEBUI / 'static', path[len('/static/'):]
            try:
                target = _safe_join(root, rel)
            except ValueError:
                return self._err(404, 'not found')
            if not target.is_file():
                return self._err(404, 'not found')
            mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            if mime.startswith('text/') or mime in ('application/javascript', 'application/json'):
                mime += '; charset=utf-8'
            try:
                data = target.read_bytes()
            except OSError as exc:
                return self._err(404, exc)
            self._send(200, data, mime, {'Cache-Control': 'no-cache'})

        def _art(self, name):
            if name == 'icon':
                target = next((p for p in _icon_candidates() if p.is_file()), None)
                if target is None:
                    return self._err(404, 'not found')
            else:
                try:
                    target = _safe_join(SPRITES, name)
                except ValueError:
                    return self._err(404, 'not found')
            if not target.is_file():
                return self._err(404, 'not found')
            try:
                data = target.read_bytes()
            except OSError as exc:
                return self._err(404, exc)
            mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self._send(200, data, mime, {'Cache-Control': 'private, max-age=3600'})

    return Handler


def _equal(a, b):
    import hmac
    return hmac.compare_digest(str(a or ''), str(b or ''))


def _safe_join(root, rel):
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError('outside the root')
    return target


def _icon_candidates():
    """The panel icon: bundled webui asset first, then the source tree's data/,
    then the icon theme of an installed Yumi."""
    yield WEBUI / 'static' / 'img' / 'icon.png'
    yield Path(config.HERE) / 'data' / 'io.github.lamex30.Yumi.png'
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    for base in (data_home, Path('/usr/local/share'), Path('/usr/share')):
        yield base / 'icons' / 'hicolor' / '256x256' / 'apps' / 'io.github.lamex30.Yumi.png'
