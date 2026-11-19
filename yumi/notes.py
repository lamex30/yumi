"""Her notes: a small sqlite book with a vector recall over it.

A note may come from the browser panel (typed by hand) or from her ears (the
user said «запиши заметку ...»).  Every note keeps its text, a title and the
same two vectors the talk diary uses (see ``yumi/memory.py``): Ollama's
embeddings when they answer, a hashed bag-of-words otherwise.  The search is
hybrid — the words' closeness leads, the mind's embeddings add on top — so a
note can be found by a paraphrase as well as by its own words.

Pure stdlib: sqlite3 + urllib, no GTK here.  The browser panel talks to this
book on its HTTP thread; her ears do the same from their worker, so a slow
embedder never freezes the moth.
"""
import json
import math
import re
import sqlite3
import struct
import threading
import time
import urllib.request
import zlib

from .chat import DEFAULT_URL
from .config import STATE_DIR
from .memory import (EMB_RETRY, EMB_TIMEOUT, FALLBACK_DIM, MODEL_BASE,
                     STOPWORDS, _cos, _norm, _pack, _prefix, _stem, _tokens,
                     _unpack)

DB_FILE = STATE_DIR / 'yumi.db'
MAX_NOTES = 5000         # the book is a notebook, not an archive
NOTE_ROWS = 1500         # how many of the newest notes take part in a search
NOTE_FLOOR = 0.12        # a hit must beat this to count
NOTE_MAX = 20000         # a note's text, characters
TITLE_MAX = 120
SOURCES = ('web', 'voice', 'api')

# the words that make a note out of a spoken phrase, longest first: the text
# after the trigger is the note itself, the trigger goes
_ADD_TRIGGERS = (
    'запиши это в заметки', 'запиши в заметки', 'запиши в заметку',
    'запиши заметку', 'сохрани заметку', 'создай заметку', 'добавь заметку',
    'сделай заметку', 'новая заметка', 'заметка:', 'заметка',
    'запиши:', 'запиши',
    'write a note', 'write note', 'make a note', 'add a note', 'add note',
    'save a note', 'save note', 'new note', 'note down', 'take a note', 'note:',
)
_SEARCH_TRIGGERS = (
    'что я писала в заметках', 'что я писал в заметках',
    'что в моих заметках про', 'что в моих заметках о', 'что в моих заметках',
    'что в заметках про', 'что в заметках о', 'что в заметках',
    'найди заметку про', 'найди заметку о', 'найди заметку',
    'найди в заметках', 'поищи в заметках', 'поищи заметку',
    'покажи заметки про', 'покажи заметки о', 'мои заметки про', 'мои заметки о',
    'заметки про', 'заметки о',
    'what do my notes say about', 'search notes for', 'find a note about',
    'find note about', 'find a note', 'notes about', 'notes on',
)
_LIST_TRIGGERS = (
    'покажи все заметки', 'покажи заметки', 'мои заметки', 'список заметок', 'все заметки',
    'show all notes', 'show my notes', 'show notes', 'my notes', 'list notes', 'all notes',
)
_LAST_TRIGGERS = (
    'покажи последнюю заметку', 'прочитай последнюю заметку', 'озвучь последнюю заметку',
    'зачитай последнюю заметку', 'какая у меня последняя заметка', 'какая последняя заметка',
    'что в последней заметке', 'что я писала последнее', 'что я писал последнее',
    'самая свежая заметка', 'самая последняя заметка', 'последняя заметка',
    'последнюю заметку', 'свежая заметка',
    'what was my last note', 'what was the last note', 'what is my last note',
    'what is the last note', "what's my last note", "what's the last note",
    'show my last note', 'show the last note', 'show the latest note',
    'read my last note', 'read the last note', 'read the latest note',
    'my last note', 'the last note', 'latest note', 'last note',
)
# the ears bend the endings («последний заметка», «последние заметки») and may
# keep her name at the head («юлии последняя заметка»): the stems still say it
_LAST_RE = re.compile(
    r'\bпоследн\w*\s+заметк\w*'
    r'|\bсвеж\w*\s+заметк\w*'
    r'|\b(?:last|latest)\s+notes?\b',
    re.IGNORECASE)
# a wish may come with a polite word before it («пожалуйста, запиши заметку…»):
# only the verbs take part, so a sentence that merely mentions a «заметка» is
# not turned into a note
_MID_ADD_TRIGGERS = tuple(
    t for t in _ADD_TRIGGERS
    if t.startswith(('запиши', 'сохрани', 'создай', 'добавь',
                     'write', 'make', 'add', 'save', 'take', 'note down')))


class Notes:
    def __init__(self):
        self.lock = threading.Lock()
        self._fail_until = 0.0          # the hashed fallback is trusted until this moment
        self._open()

    def _open(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(DB_FILE), timeout=5, check_same_thread=False)
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS notes ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,'
            'updated REAL NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,'
            'source TEXT NOT NULL, dim INTEGER, emb BLOB, bagemb BLOB)')
        self.con.commit()

    # ---------------------------------------------------------------- the book
    def add(self, text, settings, title=None, source='web'):
        """a new note, with both vectors beside it; returns the note as the panel sees it"""
        text = _clean_text(text)
        if not text:
            raise ValueError('a note needs some text')
        title = _title(title, text)
        source = source if source in SOURCES else 'web'
        vec, dim, _kind = self._embed(text, settings, kind='passage')
        bagvec, _ = self._hashvec(text)
        now = time.time()
        with self.lock:
            try:
                cur = self.con.execute(
                    'INSERT INTO notes (ts, updated, title, text, source, dim, emb, bagemb) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (now, now, title, text, source, dim, _pack(vec), _pack(bagvec)))
                self.con.execute(
                    'DELETE FROM notes WHERE id NOT IN '
                    '(SELECT id FROM notes ORDER BY id DESC LIMIT ?)', (MAX_NOTES,))
                self.con.commit()
                note_id = cur.lastrowid
            except sqlite3.Error as exc:
                raise ValueError(f'the note was not saved: {exc}')
        return self._one(note_id) or {'id': note_id, 'title': title, 'text': text,
                                      'source': source, 'ts': now, 'updated': now}

    def update(self, note_id, text, settings, title=None):
        """rewrite a note and its vectors; None when there is no such note"""
        text = _clean_text(text)
        if not text:
            raise ValueError('a note needs some text')
        vec, dim, _kind = self._embed(text, settings, kind='passage')
        bagvec, _ = self._hashvec(text)
        with self.lock:
            try:
                row = self.con.execute(
                    'SELECT title, source FROM notes WHERE id=?', (int(note_id),)).fetchone()
                if not row:
                    return None
                title = _title(title or row[0], text)
                self.con.execute(
                    'UPDATE notes SET title=?, text=?, updated=?, dim=?, emb=?, bagemb=? '
                    'WHERE id=?',
                    (title, text, time.time(), dim, _pack(vec), _pack(bagvec), int(note_id)))
                self.con.commit()
            except (sqlite3.Error, TypeError, ValueError):
                return None
        return self._one(note_id)

    def delete(self, note_id):
        """drop one note; True when it was there"""
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            try:
                cur = self.con.execute('DELETE FROM notes WHERE id=?', (note_id,))
                self.con.commit()
                return cur.rowcount > 0
            except sqlite3.Error:
                return False

    def clear(self):
        """empty the book (the panel's explicit button); the count gone"""
        with self.lock:
            try:
                cur = self.con.execute('DELETE FROM notes')
                self.con.commit()
                return cur.rowcount
            except sqlite3.Error:
                return 0

    def count(self):
        with self.lock:
            try:
                return int(self.con.execute('SELECT COUNT(*) FROM notes').fetchone()[0])
            except sqlite3.Error:
                return 0

    def recent(self, limit=50):
        """the newest notes for the browser panel"""
        limit = max(1, min(int(limit or 50), 500))
        with self.lock:
            try:
                rows = self.con.execute(
                    'SELECT id, ts, updated, title, text, source FROM notes '
                    'ORDER BY updated DESC, id DESC LIMIT ?', (limit,)).fetchall()
            except sqlite3.Error:
                return []
        return [_row(r) for r in rows]

    def get(self, note_id):
        return self._one(note_id)

    def search(self, query, settings, limit=50):
        """the notes about the same thing as `query`: the closest `limit` of them,
        scored the same hybrid way as the talk diary ([] when the book is empty
        or nothing is close enough)"""
        query = ' '.join(str(query or '').split())
        if not query:
            return self.recent(limit)
        limit = max(1, min(int(limit or 50), 200))
        vec, dim, _kind = self._embed(query, settings, kind='query')
        bagvec, _ = self._hashvec(query)
        with self.lock:
            try:
                rows = self.con.execute(
                    'SELECT id, ts, updated, title, text, source, dim, emb, bagemb '
                    'FROM notes ORDER BY updated DESC, id DESC LIMIT ?',
                    (NOTE_ROWS,)).fetchall()
            except sqlite3.Error:
                return []
        found = []
        for r in rows:
            note_id, ts, updated, title, text, source, rdim, emb, bagemb = r
            score = _cos(bagvec, _unpack(bagemb, len(bagvec)))
            if rdim == dim:
                model = _cos(vec, _unpack(emb, dim)) - MODEL_BASE
                if model > 0:
                    score += model
            if score >= NOTE_FLOOR:
                found.append((score, _row((note_id, ts, updated, title, text, source))))
        found.sort(key=lambda r: (-r[0], -r[1]['updated']))
        return [dict(note, score=round(score, 3)) for score, note in found[:limit]]

    def _one(self, note_id):
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return None
        with self.lock:
            try:
                row = self.con.execute(
                    'SELECT id, ts, updated, title, text, source FROM notes WHERE id=?',
                    (note_id,)).fetchone()
            except sqlite3.Error:
                return None
        return _row(row) if row else None

    # ---------------------------------------------------------------- the vectors
    def _embed(self, text, settings, kind='passage'):
        """(vector, dim, kind) of a note: Ollama's embeddings when they answer,
        the hashed bag-of-words otherwise (and for a while after any failure)"""
        if time.time() >= self._fail_until:
            model = str(settings.get('embed_model') or 'nomic-embed-text')
            try:
                url = str(settings.get('ollama_url') or DEFAULT_URL).rstrip('/') \
                      + '/api/embeddings'
                body = {'model': model,
                        'prompt': _prefix(model, kind) + str(text)[:800]}
                req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=EMB_TIMEOUT) as r:
                    emb = json.loads(r.read().decode()).get('embedding')
                vec = [float(x) for x in emb or []]
                if vec:
                    return _norm(vec), len(vec), 'model'
            except (OSError, ValueError, KeyError):
                pass
            self._fail_until = time.time() + EMB_RETRY
        return (*self._hashvec(text), 'bag')

    def _hashvec(self, text):
        """a stand-in vector: the word stems hashed into a small basket"""
        vec = [0.0] * FALLBACK_DIM
        counts = {}
        for w in (_stem(w) for w in _tokens(text) if w not in STOPWORDS):
            counts[w] = counts.get(w, 0) + 1
        for w, n in counts.items():
            vec[zlib.crc32(w.encode()) % FALLBACK_DIM] += math.sqrt(n)
        return _norm(vec), FALLBACK_DIM


def _clean_text(text):
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    return text[:NOTE_MAX]


def _title(title, text):
    title = ' '.join(str(title or '').split())
    if not title:
        first = next((line.strip() for line in text.splitlines() if line.strip()), text)
        title = first[:80]
    return title[:TITLE_MAX]


def _row(row):
    note_id, ts, updated, title, text, source = row
    return {'id': note_id, 'ts': ts, 'updated': updated, 'title': title,
            'text': text, 'source': source}


def brief(text, limit=260):
    """the first sentences of a long note: what she reads when the mind is away"""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '), cut.rfind('; '))
    if end >= limit // 2:
        return cut[:end + 1].strip()
    return cut.rstrip(' ,;:—–-') + '…'


def voice_intent(text, lang='ru'):
    """what a spoken phrase asks of the notes: {'action': 'add', 'text': ...},
    {'action': 'search', 'query': ...}, {'action': 'list'} or None.

    The trigger is cut off and the rest is kept as it was said (with its case
    and punctuation): that is the note's text, or the words to look for."""
    raw = ' '.join(str(text or '').split())
    if not raw:
        return None
    low = raw.lower().replace('ё', 'е')
    for action, triggers in (('last', _LAST_TRIGGERS),
                             ('search', _SEARCH_TRIGGERS),
                             ('list', _LIST_TRIGGERS),
                             ('add', _ADD_TRIGGERS)):
        for trigger in sorted(triggers, key=len, reverse=True):
            if low.startswith(trigger):
                body = raw[len(trigger):].strip(' \t:-,.!?—–')
                if not body and action == 'search':
                    return {'action': 'list'}
                if action in ('list', 'last'):
                    return {'action': action}
                return {'action': action, 'text': body} if action == 'add' \
                    else {'action': action, 'query': body}
    if _LAST_RE.search(raw):           # «последний заметка», «юлии последняя заметка»
        return {'action': 'last'}
    # «запиши заметку …» said with a word before it («пожалуйста, запиши…»)
    for trigger in sorted(_MID_ADD_TRIGGERS, key=len, reverse=True):
        at = low.find(' ' + trigger)
        if at >= 0:
            body = raw[at + 1 + len(trigger):].strip(' \t:-,.!?—–')
            return {'action': 'add', 'text': body}
    return None


# ---- the book as it is called from her side: one book for the process ----
_BOOK = None
_BOOK_LOCK = threading.Lock()


def _book():
    global _BOOK
    with _BOOK_LOCK:
        if _BOOK is None:
            _BOOK = Notes()
        return _BOOK


def add(text, settings, title=None, source='web'):
    return _book().add(text, settings, title=title, source=source)


def update(note_id, text, settings, title=None):
    return _book().update(note_id, text, settings, title=title)


def delete(note_id):
    return _book().delete(note_id)


def clear():
    return _book().clear()


def count():
    return _book().count()


def recent(limit=50):
    return _book().recent(limit)


def get(note_id):
    return _book().get(note_id)


def search(query, settings, limit=50):
    return _book().search(query, settings, limit=limit)
