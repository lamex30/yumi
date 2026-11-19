#!/usr/bin/env python3
"""The browser panel's HTTP face: catalog, lock, CSRF, settings and static files.
Run: python3 tests/test_webpanel.py"""
import http.cookiejar
import json
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import config  # noqa: E402
from yumi.webpanel import auth, catalog, server  # noqa: E402

assert __import__('yumi').__version__


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Bridge:
    """The smallest stand-in for yumi.ui.bridge.WebBridge."""

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else dict(config.DEFAULT_SETTINGS)
        self.applied = []
        self.saved = []
        self.voice_calls = []
        self.allowed = set()
        self.actions = [{'label': 'Term', 'label_ru': 'Терм', 'icon': 'terminal',
                         'command': 'foot', 'phrases': []}]
        self.note_items = []
        self.note_id = 0

    def file_allowed(self, path):
        try:
            return Path(str(path)).expanduser().resolve() in self.allowed
        except (OSError, RuntimeError, ValueError):
            return False

    def _snap(self):
        return {
            'time': 1.0, 'version': 'test', 'uptime': 12,
            'settings': catalog.redact(self.settings),
            'settings_full': catalog.redact(self.settings),
            'state': {'mode': 'docked', 'monitor': None, 'shelf': [], 'colors': []},
            'runtime': {'mode': 'docked', 'out': True, 'sleeping': False,
                        'pinned': False, 'thinking': False, 'listening': False,
                        'busy': False, 'progress': False, 'bubble': 'привет',
                        'face': 'idle', 'monitor': None, 'monitors': 1,
                        'chat_models': [], 'mics': [], 'unavailable': [],
                        'palette': {}, 'ui_colors': {'accent': '#c9a7e0'},
                        'music': {'now_playing': None, 'volume': 50},
                        'wake_on': True, 'tap_ears': False, 'downloads': False,
                        'outfit': 'auto', 'shelf_count': 0, 'recent_count': 0,
                        'timer': {'seconds': 0, 'text': '', 'running': False,
                                  'pending': False, 'paused': False, 'done': False}},
            'jobs': {'busy': False, 'current': None, 'queued': []},
            'panel': {'running': True},
            'features': {'ffmpeg': True},
        }

    def snapshot(self):
        return self._snap()

    def configs(self):
        return [{'name': 'settings', 'path': '/tmp/settings.json', 'exists': True,
                 'size': 2, 'mtime': 1.0, 'title': {'ru': 'Настройки'}, 'kind': 'settings'}]

    def config_get(self, name):
        return {'file': self.configs()[0], 'data': {}, 'text': '{}', 'writable': True}

    def config_save(self, name, value):
        self.saved.append((name, value))
        return {'saved': True, 'backup': '/tmp/backup.json'}

    def apply_settings(self, patch):
        self.applied.append(patch)
        self.settings.update(patch)
        return {'saved': patch, 'before': {}, 'restart': 'scale' in patch}

    def actions_get(self):
        return {'actions': self.actions, 'ring': [
            {'id': 'recent', 'label': 'Recent', 'icon': 'folder', 'custom': False,
             'off': False, 'fixed': False}],
            'ring_order': [], 'ring_off': [], 'icons': ['folder'],
            'icon_patterns': {'folder': ['#######'] * 7}}

    def actions_save(self, actions):
        self.actions = actions
        return {'saved': len(actions), 'backup': ''}

    def notes_list(self, limit=50):
        return list(reversed(self.note_items))[:limit]

    def notes_search(self, query, limit=50):
        q = str(query or '').lower()
        return [n for n in self.notes_list(limit) if q in n['text'].lower()]

    def notes_get(self, note_id):
        return next((n for n in self.note_items if n['id'] == int(note_id)), None)

    def notes_add(self, text, title=None, source='web'):
        self.note_id += 1
        note = {'id': self.note_id, 'title': title or str(text)[:80],
                'text': str(text), 'source': source, 'ts': 1.0, 'updated': 1.0}
        self.note_items.append(note)
        return note

    def notes_update(self, note_id, text, title=None):
        note = self.notes_get(note_id)
        if not note:
            return None
        note['text'] = str(text)
        if title:
            note['title'] = title
        return note

    def notes_delete(self, note_id):
        note = self.notes_get(note_id)
        if not note:
            raise ValueError('there is no such note')
        self.note_items.remove(note)
        return {'deleted': True, 'id': int(note_id)}

    def notes_clear(self):
        gone = len(self.note_items)
        self.note_items = []
        return {'deleted': gone}

    def logs(self, name, lines):
        if name not in ('yumi', 'judge', 'ears', 'web'):
            raise ValueError('unknown log')
        return {'name': name, 'path': '/tmp/yumi.log', 'text': 'line\n' * 3}

    def voice_engines(self):
        return {'activation': {'oww': {'available': False}, 'vosk': {'available': True}},
                'judge': {'whisper': {'available': True}, 'nemotron': {'available': False}},
                'tts': {'available': False}}

    def voice_mics(self):
        self.voice_calls.append(('mics',))
        return {'mics': [{'name': 'plughw:1,0', 'label': 'Headset'}]}

    def voice_listen(self, seconds, engine=None):
        self.voice_calls.append(('listen', seconds, engine))
        return {'heard': 'фраза', 'engine': engine}

    def voice_test_wake(self, seconds, ear=None):
        self.voice_calls.append(('wake', seconds, ear))
        return {'woke': True, 'text': '', 'ear': ear, 'word': 'юми',
                'seconds': seconds, 'elapsed': 1.0}

    def voice_test(self, text=None, lang=None):
        self.voice_calls.append(('tts', text, lang))
        return {'text': text or 'sample', 'lang': lang, 'profile': {'pitch': 1.12}}

    def state_action(self, name, payload):
        return {'action': name}

    def __getattr__(self, name):
        def stub(*_a, **_k):
            return {} if not name.startswith('files_') else []
        return stub


tmp = Path(tempfile.mkdtemp(prefix='yumi-webpanel-'))
orig_state = config.STATE_DIR
config.STATE_DIR = tmp
settings = dict(config.DEFAULT_SETTINGS)
settings['web_panel'] = True
settings['web_panel_host'] = '127.0.0.1'
settings['web_panel_port'] = free_port()
bridge = Bridge(settings)
panel = server.Panel(settings, bridge, config_dir=tmp)
assert panel.start(), panel.error
base = f'http://127.0.0.1:{panel.port}'

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, csrf=None, raw=False):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if csrf:
        headers['X-Yumi-CSRF'] = csrf
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=10) as res:
            payload = res.read()
            return res.status, (payload if raw else json.loads(payload or b'null')), res.headers
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b'null')
        except ValueError:
            parsed = payload
        return exc.code, parsed, exc.headers


try:
    # ---- catalog: every default is in the form map, every page exists
    schema = catalog.schema()
    keys = {entry['key'] for entry in schema['settings']}
    assert set(config.DEFAULT_SETTINGS) <= keys, set(config.DEFAULT_SETTINGS) - keys
    assert {'web_panel', 'web_panel_host', 'web_panel_port', 'web_panel_open',
            'web_panel_session_hours'} <= keys
    assert len(schema['pages']) == 13
    assert {page['id'] for page in schema['pages']} == {
        'dashboard', 'chat', 'notes', 'files', 'music', 'voice', 'buttons', 'appearance',
        'settings', 'memory', 'configs', 'security', 'logs'}
    entries = {entry['key']: entry for entry in schema['settings']}
    assert entries['wake_sensitivity']['type'] == 'select'
    assert entries['ui_lang']['type'] == 'select'
    assert entries['stt_echo']['type'] == 'switch'
    assert entries['voice_vol']['type'] == 'range'
    assert entries['tts_pitch']['type'] == 'range'
    assert entries['tts_ru_speaker']['type'] == 'select'
    assert entries['tts_en_speaker']['choices'][0] == 'en_0'
    assert entries['tts_ru_model']['type'] == 'select'
    assert catalog.validate_settings_patch({'tts_pitch': '1.3'}) == {'tts_pitch': 1.3}
    try:
        catalog.validate_settings_patch({'tts_pitch': 3})
        raise AssertionError('bad pitch accepted')
    except ValueError:
        pass
    assert catalog.redact({'polza_api_key': 'secret', 'music_jellyfin_password': 'x'})['polza_api_key'] == catalog.MASK
    assert catalog.validate_settings_patch({'web_panel': True, 'web_panel_port': '9000'}) == {
        'web_panel': True, 'web_panel_port': 9000}
    try:
        catalog.validate_settings_patch({'nonsense': 1})
        raise AssertionError('unknown key accepted')
    except ValueError:
        pass
    try:
        catalog.validate_settings_patch({'web_panel_port': 5})
        raise AssertionError('bad port accepted')
    except ValueError:
        pass

    # ---- auth: signed sessions, CSRF, token rotation keeps sessions, rate limit
    store = tmp / 'auth.json'
    a = auth.Auth(store, 1)
    assert len(a.token) >= 24 and oct(store.stat().st_mode & 0o777) == '0o600'
    session, csrf, exp = a.issue()
    assert a.verify(session) and not a.verify(session + 'x') and a.csrf_for(session) == csrf
    old_token = a.token
    restarted = auth.Auth(store, 1)
    assert restarted.verify(session), 'a restart must keep signed sessions'
    restarted.rotate()
    assert restarted.token != old_token and restarted.verify(session)
    for _ in range(10):
        restarted.fail('1.2.3.4')
    assert restarted.blocked('1.2.3.4') and not restarted.blocked('5.6.7.8')
    restarted.success('1.2.3.4')
    assert not restarted.blocked('1.2.3.4')

    # ---- HTTP: public surface, login, CSRF, settings, static
    code, data, _ = call('GET', '/api/session')
    assert code == 200 and data['authed'] is False and data['version']
    code, data, _ = call('GET', '/api/status')
    assert code == 401, (code, data)
    code, data, _ = call('POST', '/api/login', {'token': 'wrong'})
    assert code == 403
    code, data, _ = call('POST', '/api/login', {'token': panel.auth.token})
    assert code == 200 and data['csrf'], (code, data)
    csrf = data['csrf']
    cookie = next(c.value for c in jar if c.name == auth.CSRF_COOKIE)
    assert cookie == csrf

    code, data, _ = call('GET', '/api/status')
    assert code == 200 and data['runtime']['bubble'] == 'привет'
    code, data, _ = call('GET', '/api/schema')
    assert code == 200 and data['version'] and data['pages']
    code, data, _ = call('GET', '/api/actions')
    assert code == 200 and data['icon_patterns']['folder']

    code, data, _ = call('PATCH', '/api/settings', {'patch': {'chatter': False}})
    assert code == 403, 'a mutation without CSRF must fail'
    code, data, _ = call('PATCH', '/api/settings', {'patch': {'chatter': False}}, csrf=csrf)
    assert code == 200 and bridge.applied[-1] == {'chatter': False}
    assert data['settings']['chatter'] is False

    code, data, _ = call('GET', '/api/config/settings')
    assert code == 200 and data['writable'] and data['text'] == '{}'
    code, data, _ = call('PUT', '/api/config/settings',
                         {'data': {'chatter': False}}, csrf=csrf)
    assert code == 200 and bridge.saved[-1] == ('settings', {'chatter': False})
    code, data, _ = call('GET', '/api/config/nope')
    assert code == 400

    # voice pickers: engines, activation test, judge test, TTS sample
    code, data, _ = call('GET', '/api/voice/engines')
    assert code == 200 and data['activation']['vosk']['available'] is True
    code, data, _ = call('POST', '/api/voice/test-wake',
                         {'seconds': 5, 'ear': 'vosk'}, csrf=csrf)
    assert code == 200 and data['woke'] is True
    code, data, _ = call('POST', '/api/voice/listen',
                         {'seconds': 5, 'engine': 'nemotron'}, csrf=csrf)
    assert code == 200 and data['engine'] == 'nemotron'
    code, data, _ = call('POST', '/api/voice/test',
                         {'text': 'Привет', 'lang': 'ru'}, csrf=csrf)
    assert code == 200 and data['lang'] == 'ru'
    assert bridge.voice_calls[-3:] == [('wake', 5.0, 'vosk'), ('listen', 5.0, 'nemotron'),
                                      ('tts', 'Привет', 'ru')], bridge.voice_calls
    code, data, _ = call('GET', '/api/voice/mics')
    assert code == 200 and data['mics'] == [{'name': 'plughw:1,0', 'label': 'Headset'}], data

    # notes: list, add (CSRF), search, edit, delete, clear
    code, data, _ = call('GET', '/api/notes')
    assert code == 200 and data['items'] == []
    code, data, _ = call('POST', '/api/notes', {'text': 'купить молоко'})
    assert code == 403, 'a note without CSRF must fail'
    code, data, _ = call('POST', '/api/notes',
                         {'text': 'Купить молоко и хлеб', 'title': 'Покупки'}, csrf=csrf)
    assert code == 200 and data['note']['id'] == 1, (code, data)
    note_id = data['note']['id']
    code, data, _ = call('POST', '/api/notes', {'text': 'Созвон с врачом'}, csrf=csrf)
    assert code == 200 and data['note']['id'] == 2
    code, data, _ = call('GET', '/api/notes?q=' + urllib.parse.quote('молоко'))
    assert code == 200 and [n['id'] for n in data['items']] == [note_id], data
    code, data, _ = call('GET', f'/api/notes/{note_id}')
    assert code == 200 and data['note']['title'] == 'Покупки'
    code, data, _ = call('PATCH', f'/api/notes/{note_id}',
                         {'text': 'Купить молоко, хлеб и сыр', 'title': 'Список'}, csrf=csrf)
    assert code == 200 and data['note']['text'].endswith('сыр')
    code, data, _ = call('POST', '/api/notes/clear', {}, csrf=csrf)
    assert code == 400, 'clearing needs "all": true'
    code, data, _ = call('DELETE', f'/api/notes/{note_id}', csrf=csrf)
    assert code == 200 and data['deleted'] is True
    code, data, _ = call('DELETE', f'/api/notes/{note_id}', csrf=csrf)
    assert code == 400, (code, data)
    code, data, _ = call('POST', '/api/notes/clear', {'all': True}, csrf=csrf)
    assert code == 200 and data['deleted'] == 1, (code, data)

    code, data, _ = call('GET', '/api/security/link')
    assert code == 404, 'the link must only be handed out by an explicit POST'
    code, data, _ = call('POST', '/api/security/link', csrf=csrf)
    assert code == 200 and '?t=' in data['url']
    code, data, _ = call('POST', '/api/security/rotate', csrf=csrf)
    assert code == 200 and panel.auth.token in data['url']
    code, data, _ = call('GET', '/api/status')
    assert code == 200, 'rotation must keep this session signed in'

    code, body, headers = call('GET', '/', raw=True)
    assert code == 200 and b'YUMI' in body
    assert 'Content-Security-Policy' in headers
    code, body, _ = call('GET', '/static/js/app.js', raw=True)
    assert code == 200 and b'PAGES' in body
    code, body, _ = call('GET', '/static/js/pages/dashboard.js', raw=True)
    assert code == 200 and b'export function render' in body
    code, body, _ = call('GET', '/static/js/pages/notes.js', raw=True)
    assert code == 200 and b'export function render' in body
    code, body, _ = call('GET', '/static/css/app.css', raw=True)
    assert code == 200 and b'--accent' in body
    code, body, headers = call('GET', '/art/icon', raw=True)
    assert code == 200 and headers.get('Content-Type', '').startswith('image/'), (code, headers)
    assert body.startswith(b'\x89PNG')
    code, body, headers = call('GET', '/favicon.ico', raw=True)
    assert code == 200 and headers.get('Content-Type', '').startswith('image/'), (code, headers)
    code, body, _ = call('GET', '/../moth_girl.py', raw=True)
    assert code == 404

    code, data, _ = call('GET', '/api/logs?name=nope')
    assert code == 400
    code, data, _ = call('GET', '/api/nope')
    assert code == 404

    # previews are limited to files the panel knows (recent, shelf, uploads)
    img = tmp / 'sample.png'
    img.write_bytes(b'\x89PNG\r\n\x1a\n')
    bridge.allowed.add(img.resolve())
    quoted = urllib.parse.quote(str(img))
    code, body, headers = call('GET', f'/api/file/preview?path={quoted}', raw=True)
    assert code == 200 and headers.get('Content-Type', '').startswith('image/'), (code, headers)
    stranger = tmp / 'stranger.png'
    stranger.write_bytes(b'\x89PNG\r\n\x1a\n')
    quoted = urllib.parse.quote(str(stranger))
    code, data, _ = call('GET', f'/api/file/preview?path={quoted}')
    assert code == 403, (code, data)

    # logout clears the cookies
    code, data, headers = call('POST', '/api/logout', csrf=csrf)
    assert code == 200
    code, data, _ = call('GET', '/api/status')
    assert code == 401

    print('ok')
finally:
    panel.stop()
    config.STATE_DIR = orig_state
    shutil.rmtree(tmp, ignore_errors=True)
