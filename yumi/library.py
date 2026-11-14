"""Her music libraries: Jellyfin when at home, local folders when away.

Two sources with one shape: search(name), by_artist(name), by_album(name),
random(n) — every answer is a list of tracks
{'title', 'artist', 'album', 'url' or 'path', 'source'}.
A track from Jellyfin carries a stream url the player adds as is; a track from
the folders carries a file path. resolve() picks the source: when Jellyfin is
configured and answers, the songs come from its big library, otherwise she
reads her own folders — «дома из большой, не дома из своей».

No new dependencies: the web is urllib, the tags are mutagen when it happens
to be installed, the index lives in ~/.cache/yumi/library.json.
"""

import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUDIO_EXT = ('.mp3', '.flac', '.ogg', '.oga', '.opus', '.wav', '.m4a', '.wma',
             '.aac', '.aif', '.aiff', '.ape')
CACHE_FILE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') \
    / 'yumi' / 'library.json'
RESCAN_AFTER = 600          # s; younger than that, the index is trusted as is
_CLIENT = 'MediaBrowser Client="Yumi", Device="Yumi", DeviceId="yumi-moth", Version="1.0"'


def _tokens(text):
    """the words of a name the way the search reads them: lower case, ё folded
    into е, punctuation gone (the same kindness nlu.normalize keeps)"""
    return [t.lower().replace('ё', 'е') for t in re.split(r'\W+', str(text or ''))
            if t]


def _json(url, data=None, headers=None, timeout=4):
    """one GET or POST that answers json, or None when the net is away"""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except (OSError, urllib.error.URLError, ValueError):
        return None


def _tags(path):
    """the song's names from its tags, when mutagen is around (else None)"""
    try:
        from mutagen import File as mfile
    except ImportError:
        return None
    try:
        f = mfile(str(path), easy=True)
    except Exception:
        return None
    if not f:
        return None
    def g(k):
        v = (f.get(k) or [''])[0]
        return v.strip() if isinstance(v, str) else ''
    return {'title': g('title'), 'artist': g('artist'), 'album': g('album')}


def _name_from_file(path):
    """«Аквариум - Город золотой.mp3» → ('Аквариум', 'Город золотой'):
    the stem is the best she has when the tags are away, a number at the
    head goes («01 - »), a bare stem is the whole name"""
    stem = Path(path).stem
    artist, _, title = stem.partition(' - ')
    if title:
        return artist.strip(' 0123456789.-'), title.strip()
    return '', stem.strip(' 0123456789.-')


def _track(path, mtime=None):
    """one local track dict: the tags when they exist, the file name when not"""
    tags = _tags(path) or {}
    title = tags.get('title')
    artist = tags.get('artist')
    if not title or not artist:
        fa, ft = _name_from_file(path)
        title = title or ft
        artist = artist or fa
    return {'title': title, 'artist': artist, 'album': tags.get('album', ''),
            'path': str(path), 'source': 'local', 'mt': mtime}


class Jellyfin:
    """the big library at home: songs found in Jellyfin play as stream urls"""

    def __init__(self, settings):
        self.base = str(settings.get('music_jellyfin') or '').strip().rstrip('/')
        self.user = str(settings.get('music_jellyfin_user') or '').strip()
        self.password = str(settings.get('music_jellyfin_password') or '')
        self.key = str(settings.get('music_jellyfin_key') or '').strip()
        self.token = self.key
        self.uid = ''

    def ok(self):
        """an address is written down at all"""
        return bool(self.base)

    def available(self, timeout=1.5):
        """the server answers at its door: a plain TCP knock, no requests yet"""
        try:
            u = urllib.parse.urlparse(self.base)
            port = u.port or (443 if u.scheme == 'https' else 80)
            with socket.create_connection((u.hostname, port), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def auth(self):
        """a token to read the library with: the api key when it is written,
        otherwise her own login; True when the door opened"""
        if self.token:
            return True
        if not (self.base and self.user):
            return False
        r = _json(self.base + '/Users/AuthenticateByName',
                  data=json.dumps({'Username': self.user,
                                   'Pw': self.password}).encode('utf-8'),
                  headers={'Content-Type': 'application/json',
                           'X-Emby-Authorization': _CLIENT})
        if not r or not r.get('AccessToken'):
            return False
        self.token = r['AccessToken']
        self.uid = (r.get('User') or {}).get('Id', '')
        return True

    def _get(self, path, **params):
        if not self.auth():
            return None
        params['api_key'] = self.token
        url = self.base + path + '?' + urllib.parse.urlencode(params)
        return _json(url, headers={'X-Emby-Authorization': _CLIENT,
                                   'Authorization': _CLIENT})

    def _items(self, params, limit):
        r = self._get('/Items', Recursive='true', IncludeItemTypes='Audio',
                      Limit=limit, Fields='AlbumArtist,Artists,Album', **params)
        items = (r or {}).get('Items') or []
        out = []
        for it in items:
            artists = [a for a in (it.get('Artists') or []) if a]
            who = artists[0] if artists else it.get('AlbumArtist') or ''
            out.append({'title': it.get('Name') or '',
                        'artist': who,
                        'album': it.get('Album') or '',
                        'url': f'{self.base}/Audio/{it.get("Id")}/stream'
                               f'?api_key={self.token}&static=true',
                        'source': 'jellyfin'})
        return out

    def search(self, query, limit=5):
        """the songs whose names carry the words you said"""
        if not self.auth():
            return []
        return self._items({'SearchTerm': query}, limit)

    def by_artist(self, query, limit=25):
        """the songs of the singer you named: the found tracks are filtered by
        the artist first (a search matches the names of the songs too), and
        when nobody owns the name, the raw answers go"""
        if not self.auth():
            return []
        found = self._items({'SearchTerm': query}, 200)
        words = _tokens(query)
        def mine(t):
            hay = _tokens(f'{t["artist"]} {t["album"]}')
            return all(w in hay for w in words)
        picked = [t for t in found if mine(t)]
        return picked[:limit] if picked else found[:limit]

    def by_album(self, query, limit=40):
        """the songs of the album you named, the same filter by the album"""
        if not self.auth():
            return []
        found = self._items({'SearchTerm': query}, 200)
        words = _tokens(query)
        def mine(t):
            hay = _tokens(t['album'])
            return words and all(w in hay for w in words)
        picked = [t for t in found if mine(t)]
        return picked[:limit] if picked else found[:limit]

    def random(self, limit=1):
        """the library shuffles itself: SortBy=Random, one little request"""
        if not self.auth():
            return []
        return self._items({'SortBy': 'Random'}, limit)


class Local:
    """her own folders: the songs read from the tags (or the file names), the
    index is walked once and kept in ~/.cache/yumi/library.json"""

    def __init__(self, settings, dirs=None):
        if dirs is None:
            from .music import get_music_dir          # music never asks for her
            dirs = [d.strip() for d in str(settings.get('music_dirs') or '').split(',')
                    if d.strip()]
            root = get_music_dir()
            if root.is_dir():
                dirs.append(str(root))
        self.dirs = []
        for d in dirs:
            p = Path(os.path.expandvars(str(d))).expanduser()
            if p.is_dir():
                self.dirs.append(p)

    def tracks(self):
        """the whole known library: the index when it is fresh, a walk when not
        (the walk keeps the old entries, so only the new files are read)"""
        try:
            data = json.loads(CACHE_FILE.read_text())
            fresh = data.get('dirs') == [str(d) for d in self.dirs] \
                and time.time() - data.get('built', 0) < RESCAN_AFTER
            if fresh:
                return data.get('tracks') or []
        except (OSError, ValueError):
            data = {}
        seen = {t['path']: t for t in (data.get('tracks') or [])}
        out = []
        for d in self.dirs:
            for root, _, files in os.walk(d):
                for name in sorted(files):
                    if not name.lower().endswith(AUDIO_EXT):
                        continue
                    p = os.path.join(root, name)
                    try:
                        mt = os.path.getmtime(p)
                    except OSError:
                        continue
                    old = seen.get(p)
                    out.append(old if old and old.get('mt') == mt
                               else _track(p, mt))
        data = {'dirs': [str(d) for d in self.dirs], 'built': time.time(),
                'tracks': out}
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            tmp.replace(CACHE_FILE)
        except OSError:
            pass
        return out

    def _ranked(self, query, limit):
        """the tracks where every word of the name is found, the best first:
        a word in the title counts twice, in the artist once, in the album half"""
        words = _tokens(query)
        if not words:
            return []
        out = []
        for t in self.tracks():
            title = ' '.join(_tokens(t['title']))
            artist = ' '.join(_tokens(t['artist']))
            album = ' '.join(_tokens(t['album']))
            if not all(w in f'{title} {artist} {album}' for w in words):
                continue
            score = 0.0
            for w in words:
                if w in title:
                    score += 2
                if w in artist:
                    score += 1
                if w in album:
                    score += 0.5
            out.append((score, t))
        out.sort(key=lambda st: -st[0])
        return [t for _, t in out[:limit]]

    def search(self, query, limit=5):
        return self._ranked(query, limit)

    def by_artist(self, query, limit=25):
        """the songs of the singer you named, the same library ranked by the
        artist first (a search matches the names of the songs too)"""
        words = _tokens(query)
        picked, rest = [], []
        for t in self.tracks():
            hay = ' '.join(_tokens(f'{t["artist"]} {t["album"]}'))
            if words and all(w in hay for w in words):
                picked.append(t)
        if not picked:
            picked = self._ranked(query, limit)
        return picked[:limit]

    def by_album(self, query, limit=40):
        """the songs of the album you named, ranked by the album"""
        words = _tokens(query)
        picked = [t for t in self.tracks()
                  if words and all(w in ' '.join(_tokens(t['album']))
                                   for w in words)]
        return (picked or self._ranked(query, limit))[:limit]

    def random(self, limit=1):
        """n songs out of the folder, a fresh deal every time"""
        tracks = self.tracks()
        if not tracks:
            return []
        return random.sample(tracks, min(limit, len(tracks)))


def pick(settings):
    """which library she reads now: Jellyfin when it is written down, awake
    and logged in — her folders the rest of the time (a dead Jellyfin is
    never a silence: the folders answer instead)"""
    want = str(settings.get('music_source') or 'auto')
    jf = Jellyfin(settings)
    if want != 'local' and jf.ok() and jf.available():
        if jf.auth():
            return 'jellyfin', jf
    if want == 'jellyfin' and jf.ok() and jf.available() and jf.auth():
        return 'jellyfin', jf
    return 'local', Local(settings)


def resolve(settings, kind, query=''):
    """one wish against the library that is awake: a report for her bubble
    {'source', 'tracks'} — the songs already carry their urls and paths"""
    source, lib = pick(settings)
    if kind == 'random':
        tracks = lib.random(1)
    elif kind == 'artist':
        tracks = lib.by_artist(query)
    elif kind == 'album':
        tracks = lib.by_album(query)
    else:
        tracks = lib.search(query)
    return {'source': source, 'kind': kind, 'query': query, 'tracks': tracks,
            'lib': lib}
