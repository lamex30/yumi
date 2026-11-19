"""Her diary: a small sqlite book of the talks, with a vector recall over it.

Every question you ask and every answer she gives is written into
~/.local/state/yumi/yumi.db (table `messages`). When a new question comes, the
older lines that are about the same thing are recalled by vector closeness and
mixed into her mind's context — so an old topic can be picked up again, while
talks about other things stay out of the way and do not blur the answer.

The vectors come from Ollama's embeddings (settings 'embed_model') when it is
around; without it a light hashed bag-of-words stands in, so the recall works
fully offline. Pure stdlib: sqlite3 + urllib, no GTK here. She talks to the
diary away from her main thread (the same worker that carries the question to
the mind), so a slow disk or a slow embedder never freezes her.
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

DB_FILE = STATE_DIR / 'yumi.db'
MAX_ROWS = 4000          # the diary is a notebook, not an archive: the old goes out
RECALL_K = 4             # how many recalled lines may join one new question
TAG_SASSY = 'sassy'      # a line said while «Дерзкая 18+» was on: wiped with it
TAG_JOKE = 'joke'        # a joke asked for / told in her usual mood
TAG_SASSY_JOKE = 'sassy_joke'   # the same, asked for in «Дерзкая 18+»
STREAK_ROWS = 80         # how far back a run of «анекдот» requests is counted
FALLBACK_DIM = 256       # the hashed bag-of-words when no embedder is around
EMB_TIMEOUT = 3          # s: the embedder lives on the same machine, it must be quick
EMB_RETRY = 300.0        # s: how long to trust the fallback after a failed try
RECALL_ROWS = 1200       # how many of the newest lines take part in a recall
MODEL_BASE = 0.55        # the mind's idle closeness (nomic and friends are kind
                         # to everything): under it the lines are strangers
RECALL_FLOOR = 0.15      # a recall must beat this to count: the words' closeness
                         # leads, the mind's closeness only adds a little

_WORD = re.compile(r'\w+', re.UNICODE)

# the glue of a phrase: it joins everything with everything, so the recall of
# the hashed basket reads only the words that carry something
STOPWORDS = frozenset((
    'и', 'а', 'но', 'или', 'же', 'ли', 'бы', 'то', 'на', 'в', 'во', 'с', 'со', 'к', 'о',
    'об', 'от', 'до', 'по', 'за', 'у', 'из', 'для', 'что', 'чтобы', 'как', 'это', 'тот',
    'та', 'те', 'мой', 'моя', 'мне', 'меня', 'тебе', 'тебя', 'она', 'оно', 'они', 'я',
    'ты', 'мы', 'вы', 'ну', 'вот', 'еще', 'уже', 'там', 'тут', 'так', 'тоже',
    'какой', 'какая', 'какое', 'какие', 'каких', 'сколько', 'где', 'когда', 'почему',
    'зачем', 'чем', 'чей', 'кто',
    'the', 'a', 'an', 'and', 'or', 'but', 'of', 'to', 'in', 'on', 'at', 'for', 'with',
    'is', 'are', 'was', 'were', 'be', 'it', 'that', 'this', 'as', 'i', 'you', 'me',
    'my', 'your', 'she', 'we', 'so', 'do', 'did', 'have', 'has', 'will', 'would',
    'what', 'which', 'who', 'whose', 'when', 'where', 'why', 'how', 'does'))


def _tokens(text):
    """the words of a line, lower-case, ё folded to е: what the closeness reads"""
    return [w.lower().replace('ё', 'е') for w in _WORD.findall(str(text or '').lower())]


# the ends of the words (Russian and English), longest first: the recall reads
# the stems, so «погоду» and «погода» land on the same place
_ENDINGS = ('ами', 'ями', 'ому', 'ему', 'ого', 'его', 'able', 'ment', 'tion',
            'ая', 'яя', 'ое', 'ее', 'ой', 'ей', 'ий', 'ый', 'ом', 'ем', 'ах', 'ях',
            'ам', 'ям', 'ешь', 'ешь', 'ать', 'ять', 'еть', 'сть', 'ing', 'ies',
            'es', 's', 'ть', 'ла', 'ло', 'ли', 'ет', 'ут', 'ют', 'ат', 'ят', 'ит',
            'ы', 'и', 'а', 'я', 'о', 'е', 'у', 'ю', 'ь', 'й')


def _stem(w):
    """the rough stem of a word: the ending off, then cut to six letters"""
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 3:
            w = w[:-len(e)]
            break
    return w[:6]


class Memory:
    def __init__(self):
        self.lock = threading.Lock()
        self._fail_until = 0.0          # the fallback is trusted until this moment
        self._open()

    def _open(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(DB_FILE), timeout=5, check_same_thread=False)
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS messages ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,'
            'role TEXT NOT NULL, text TEXT NOT NULL, dim INTEGER, emb BLOB,'
            'bagemb BLOB, tag TEXT)')
        for column, kind in (('bagemb', 'BLOB'), ('tag', 'TEXT')):
            try:                        # a diary of the older shape gains the columns
                self.con.execute(f'ALTER TABLE messages ADD COLUMN {column} {kind}')
            except sqlite3.OperationalError:
                pass
        self.con.commit()

    # ---------------------------------------------------------------- the book
    def remember(self, role, text, settings, tag=None):
        """one line of the talk goes into the diary, with both vectors beside it;
        `tag` marks where the line came from (the jokes, the «Дерзкая 18+» talks),
        so the mode's lines can be told apart and wiped together"""
        text = ' '.join(str(text or '').split())
        if not text:
            return
        vec, dim, _kind = self._embed(text, settings, kind='passage')
        bagvec, _ = self._hashvec(text)
        with self.lock:
            try:
                self.con.execute(
                    'INSERT INTO messages (ts, role, text, dim, emb, bagemb, tag) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (time.time(), role, text[:2000], dim, _pack(vec), _pack(bagvec), tag))
                self.con.execute(
                    'DELETE FROM messages WHERE id NOT IN '
                    '(SELECT id FROM messages ORDER BY id DESC LIMIT ?)', (MAX_ROWS,))
                self.con.commit()
            except sqlite3.Error:
                pass

    def recall(self, query, settings, k=RECALL_K, skip_recent=0):
        """the older lines about the same thing as `query`: the closest `k` of them,
        in the order they were said ([] when the diary is empty or the phrase is
        brand new); the newest `skip_recent` lines are skipped — they already go
        along with the question

        The closeness is hybrid: the words' closeness (the hashed basket, which
        reads Russian endings well) leads, and the mind's embeddings add on top
        only what is above their idle kindness — so a paraphrase is found, while
        the lines that merely share the manner of speaking stay out"""
        vec, dim, _kind = self._embed(query, settings, kind='query')
        bagvec, _ = self._hashvec(query)
        with self.lock:
            try:
                sql = ('SELECT ts, role, text, dim, emb, bagemb FROM messages '
                       'WHERE bagemb IS NOT NULL AND id NOT IN '
                       '(SELECT id FROM messages ORDER BY id DESC LIMIT ?) ')
                params = [skip_recent]
                if not settings.get('sassy'):
                    # the mode is off: its lines never surface, even if a crash
                    # kept a few of them after the switch (clear_tag wipes them)
                    sql += "AND (tag IS NULL OR tag NOT LIKE 'sassy%') "
                sql += 'ORDER BY id DESC LIMIT ?'
                params.append(RECALL_ROWS)
                rows = self.con.execute(sql, params).fetchall()
            except sqlite3.Error:
                return []
        found = []
        for ts, role, text, rdim, emb, bagemb in rows:
            score = _cos(bagvec, _unpack(bagemb, len(bagvec)))
            if rdim == dim:
                model = _cos(vec, _unpack(emb, dim)) - MODEL_BASE
                if model > 0:
                    score += model
            if score >= RECALL_FLOOR:
                found.append((score, ts, role, text))
        found.sort(key=lambda r: (-r[0], -r[1]))
        return [{'role': r[2], 'text': r[3], 'ts': r[1]}
                for r in sorted(found[:k], key=lambda r: r[1])]

    def joke_streak(self, tag):
        """how many of the newest user lines in a row carry `tag` — the jokes asked
        again and again; her own replies sit between them and do not break the run,
        any other user line does"""
        with self.lock:
            try:
                rows = self.con.execute(
                    'SELECT role, tag FROM messages ORDER BY id DESC LIMIT ?',
                    (STREAK_ROWS,)).fetchall()
            except sqlite3.Error:
                return 0
        n = 0
        for role, line_tag in rows:
            if role != 'user':
                continue                # her answer to the joke is not a new request
            if line_tag != tag:
                break                   # another question: the run is over
            n += 1
        return n

    def clear_tag(self, prefix):
        """wipe every line whose tag starts with `prefix` (the «Дерзкая 18+»
        talks and jokes when the mode goes off); the count gone, 0 on failure"""
        with self.lock:
            try:
                cur = self.con.execute(
                    "DELETE FROM messages WHERE tag LIKE ?", (str(prefix) + '%',))
                self.con.commit()
                return cur.rowcount
            except sqlite3.Error:
                return 0

    def recent(self, limit=50, hide_sassy=True):
        """the newest lines of the diary for the browser panel"""
        limit = max(1, min(int(limit or 50), 500))
        sql = 'SELECT ts, role, text, tag FROM messages '
        params = []
        if hide_sassy:
            sql += "WHERE (tag IS NULL OR tag NOT LIKE 'sassy%') "
        sql += 'ORDER BY id DESC LIMIT ?'
        params.append(limit)
        with self.lock:
            try:
                rows = self.con.execute(sql, params).fetchall()
            except sqlite3.Error:
                return []
        return [{'ts': r[0], 'role': r[1], 'text': r[2], 'tag': r[3]} for r in rows]

    def clear_all(self):
        """empty the diary (the panel's explicit «forget everything» button)"""
        with self.lock:
            try:
                cur = self.con.execute('DELETE FROM messages')
                self.con.commit()
                return cur.rowcount
            except sqlite3.Error:
                return 0

    # ---------------------------------------------------------------- the vectors
    def _embed(self, text, settings, kind='passage'):
        """(vector, dim, kind) of a line: Ollama's embeddings ('model') when they
        answer, the hashed bag-of-words ('bag') otherwise (and for a while after
        any failure); kind is 'query' for the phrase just asked and 'passage'
        for the diary lines — some minds want to know which is which"""
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
        """a stand-in vector: the word stems hashed into a small basket
        (zlib.crc32), so the lines that share words land near each other"""
        vec = [0.0] * FALLBACK_DIM
        counts = {}
        for w in (_stem(w) for w in _tokens(text) if w not in STOPWORDS):
            counts[w] = counts.get(w, 0) + 1
        for w, n in counts.items():
            vec[zlib.crc32(w.encode()) % FALLBACK_DIM] += math.sqrt(n)
        return _norm(vec), FALLBACK_DIM


def _norm(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _prefix(model, kind):
    """the tag the e5 and nomic families want before a line ('query: ' / 'passage: '
    and their own words): the closeness is much sharper with it, the other minds
    read the text bare"""
    m = str(model or '').lower()
    if 'e5' in m:
        return 'query: ' if kind == 'query' else 'passage: '
    if 'nomic' in m:
        return 'search_query: ' if kind == 'query' else 'search_document: '
    return ''


def _pack(vec):
    return struct.pack('<%df' % len(vec), *vec)


def _unpack(blob, dim):
    try:
        return struct.unpack('<%df' % dim, blob)
    except (struct.error, TypeError):
        return None


def _cos(a, b):
    """closeness of two lines: 1.0 is the same line, 0.0 strangers"""
    if not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ---- the diary as it is called from her side: one book for the process ----
_BOOK = None
_BOOK_LOCK = threading.Lock()


def _book():
    global _BOOK
    with _BOOK_LOCK:
        if _BOOK is None:
            _BOOK = Memory()
        return _BOOK


def remember(role, text, settings, tag=None):
    return _book().remember(role, text, settings, tag=tag)


def recall(query, settings, k=RECALL_K, skip_recent=0):
    return _book().recall(query, settings, k=k, skip_recent=skip_recent)


def joke_streak(tag):
    return _book().joke_streak(tag)


def clear_tag(prefix=TAG_SASSY):
    return _book().clear_tag(prefix)


def recent(limit=50, hide_sassy=True):
    return _book().recent(limit, hide_sassy)


def clear_all():
    return _book().clear_all()
