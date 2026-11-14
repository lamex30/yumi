#!/usr/bin/env python3
"""The libraries she reads: her own folders found and ranked, a wish resolved
to the source that is awake. Run: python3 tests/test_library.py"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from yumi import library  # noqa: E402

tmp = Path(tempfile.mkdtemp())
(root := tmp / 'музыка').mkdir()
for name in ('Аквариум - Город золотой.mp3', 'Аквариум - Небо становится ближе.mp3',
             'Наутилус Помпилиус - Крылья.mp3', 'Мельница - Дорога сна.flac',
             'Земфира - Прогулка.ogg', 'Мельница - Ночная кобыла.mp3'):
    (root / name).touch()
sub = root / '2020 - Лучшее'
sub.mkdir()
(sub / 'Аквариум - Русская нирвана.mp3').touch()

# the index lives in the cache: it is hers, not the test's
import yumi.config as config  # noqa: E402
config.CACHE_FILE = tmp / 'library.json'
library.CACHE_FILE = config.CACHE_FILE

lib = library.Local({}, dirs=[str(root)])

# the whole library, read twice: the second time from the cache (same content)
tracks = lib.tracks()
assert len(tracks) == 7, tracks
assert {t['artist'] for t in tracks} >= {'Аквариум', 'Мельница'}
assert all(t['source'] == 'local' for t in tracks)
assert tracks == lib.tracks()

# the tags are away, so the file names are the songs: «Исполнитель - Название»
gorod = [t for t in tracks if 'золотой' in t['title'].lower()][0]
assert gorod['artist'] == 'Аквариум'
assert gorod['title'] == 'Город золотой'
assert gorod['path'].endswith('.mp3')

# a search: every word has to be found, the best match first
got = lib.search('аквариум золотой')
assert got and got[0]['title'] == 'Город золотой', got
assert all('аквариум' in f'{t["artist"]} {t["title"]}'.lower() for t in got)

# a search forgives the case (and keeps the ё where it lives)
assert lib.search('АКВАРИУМ ЗОЛОТОЙ')[0]['title'] == 'Город золотой'

# a stranger's word is no match at all
assert lib.search('кристальные скалы') == []

# a singer: her songs first, the others' never
got = lib.by_artist('аквариум', limit=10)
assert got and all(t['artist'] == 'Аквариум' for t in got), got
assert len(got) == 3          # «Русская нирвана» lives in the subfolder, too
got = lib.by_artist('мельница')
assert len(got) == 2 and {t['title'] for t in got} == {'Дорога сна', 'Ночная кобыла'}

# an album: the name asks for the album (none of these files has one)
assert lib.by_album('несуществующий альбом') == []

# a surprise: one song, then another — and they come from the library
first = lib.random(1)
assert len(first) == 1 and first[0] in tracks
assert len(lib.random(3)) == 3

# a fresh file is walked in when the cache goes stale (built long ago)
(sub / 'Мельница - Верно да.mp3').touch()
data = json.loads(config.CACHE_FILE.read_text())
data['built'] -= 10000
config.CACHE_FILE.write_text(json.dumps(data))
tracks = lib.tracks()
assert any(t['title'] == 'Верно да' for t in tracks)
assert len(tracks) == 8

# resolve: her folders answer when jellyfin is not written down anywhere
# (Local is pinned to the test folder, so no real ~/Music is walked here)
library.Local = lambda settings, dirs=None: lib
res = library.resolve({'music_source': 'auto'}, 'song', 'крылья')
assert res['source'] == 'local' and res['tracks'][0]['title'] == 'Крылья'
res = library.resolve({}, 'random')
assert res['source'] == 'local' and len(res['tracks']) == 1

# a dead jellyfin is never a silence: the folders answer instead
dead = {'music_source': 'auto', 'music_jellyfin': 'http://127.0.0.1:9', 'music_dirs': ''}
res = library.resolve(dead, 'song', 'крылья')
assert res['source'] == 'local', res

# the jellyfin client: no address, no noise (and no request leaves the house)
jf = library.Jellyfin({'music_jellyfin': ''})
assert not jf.ok() and not jf.available() and jf.search('что-нибудь') == []

# the stream url carries the server and the key, the song's own id
jf = library.Jellyfin({'music_jellyfin': 'http://192.168.0.189:8096',
                       'music_jellyfin_key': 'abc123'})
jf.token = 'abc123'
jf._get = lambda path, **kw: {'Items': [
    {'Id': 'e7', 'Name': 'Крылья', 'Artists': ['Наутилус Помпилиус'],
     'Album': 'Разлука'}]}
got = jf.search('крылья')
assert got[0]['url'] == 'http://192.168.0.189:8096/Audio/e7/stream?api_key=abc123&static=true'
assert got[0]['artist'] == 'Наутилус Помпилиус' and got[0]['title'] == 'Крылья'

# a singer over jellyfin: the tracks that own the name go first
jf._get = lambda path, **kw: {'Items': [
    {'Id': '1', 'Name': 'Крылья', 'Artists': ['Наутилус Помпилиус'], 'Album': ''},
    {'Id': '2', 'Name': 'Песня аквариум', 'Artists': ['Кто-то другой'], 'Album': ''}]}
got = jf.by_artist('наутилус')
assert len(got) == 1 and got[0]['title'] == 'Крылья'

# an unavailable server: the plain knock times out quietly, it never raises
jf = library.Jellyfin({'music_jellyfin': 'http://127.0.0.1:9'})
assert jf.available(timeout=0.2) is False

print('ok')
