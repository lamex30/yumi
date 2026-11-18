#!/usr/bin/env python3
"""The jokes in her diary: tagged with vectors, counted in a row, wiped with 18+.
Run: python3 tests/test_memory_jokes.py"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import chat, memory  # noqa: E402

assert chat.JOKE_TIRED_AFTER == 4, chat.JOKE_TIRED_AFTER
assert memory.TAG_SASSY == 'sassy'
assert memory.TAG_SASSY_JOKE.startswith(memory.TAG_SASSY)

# ---- the mind writes the brush-off: the prompt asks for the real phrase, a
# leaked placeholder is refused and the dry line stands in
seen = {}


def fake_shot(settings, user, tokens, lang='en', timeout=90, temp=None):
    seen['user'] = user
    return 'Заебал уже с анекдотами, я тебе не Слава Комиссаренко.'


chat._one_shot = fake_shot
brush = chat.joke_tired({'sassy': True}, 'ru')
assert 'стендап-комика' in seen['user'] and 'анекдот' in seen['user'], seen['user']
assert 'Заебал' in brush, brush

chat._one_shot = lambda *a, **k: '<имя комика>, отвали'
assert chat.joke_tired({}, 'ru') == '', 'a placeholder must never reach her bubble'

# ---- the diary: a throwaway book, the embedder off so the hashed vector stands
tmp = Path(tempfile.mkdtemp(prefix='yumi-memory-'))
book = None
try:
    memory.STATE_DIR = tmp
    memory.DB_FILE = tmp / 'yumi.db'
    book = memory.Memory()
    book._fail_until = time.time() + 3600.0
    s = {'sassy': True}

    for i in range(3):               # three jokes in a row in «Дерзкая 18+»
        book.remember('user', f'анекдот {i}', s, tag=memory.TAG_SASSY_JOKE)
        book.remember('assistant', f'шутка {i}', s, tag=memory.TAG_SASSY_JOKE)
    assert book.joke_streak(memory.TAG_SASSY_JOKE) == 3

    book.remember('user', 'анекдот', s, tag=memory.TAG_SASSY_JOKE)
    assert book.joke_streak(memory.TAG_SASSY_JOKE) == 4, 'the fourth must be counted'

    # both vectors are beside every joke, not just the text
    dim, emb, bagemb = book.con.execute(
        'SELECT dim, emb, bagemb FROM messages WHERE tag=? LIMIT 1',
        (memory.TAG_SASSY_JOKE,)).fetchone()
    assert dim and emb and bagemb, (dim, emb, bagemb)

    book.remember('user', 'как дела', s, tag=memory.TAG_SASSY)
    assert book.joke_streak(memory.TAG_SASSY_JOKE) == 0, 'another question ends the run'

    book.remember('user', 'анекдот', s, tag=memory.TAG_JOKE)
    assert book.joke_streak(memory.TAG_SASSY_JOKE) == 0, 'a polite joke is a different run'
    assert book.joke_streak(memory.TAG_JOKE) == 1

    # ---- the mode's lines never surface while the mode is off
    book.remember('user', 'секретная дерзкая тема про мотылька', s, tag=memory.TAG_SASSY)
    off = book.recall('секретная дерзкая тема', {'sassy': False})
    assert all('секретная' not in t['text'] for t in off), off
    on = book.recall('секретная дерзкая тема', {'sassy': True})
    assert any('секретная' in t['text'] for t in on), on

    # ---- the mode goes off: its talks and jokes go, the polite ones stay
    gone = book.clear_tag(memory.TAG_SASSY)
    assert gone == 9, gone
    left = book.con.execute(
        "SELECT COUNT(*) FROM messages WHERE tag LIKE 'sassy%'").fetchone()[0]
    assert left == 0, left
    assert book.con.execute(
        "SELECT COUNT(*) FROM messages WHERE tag=?", (memory.TAG_JOKE,)).fetchone()[0] == 1

    # ---- the module-level face of the diary works (moth_girl calls it this way)
    for name in ('remember', 'recall', 'joke_streak', 'clear_tag'):
        assert callable(getattr(memory, name)), name
    memory._BOOK = book
    memory.remember('user', 'анекдот', s, tag=memory.TAG_JOKE)
    assert memory.joke_streak(memory.TAG_JOKE) == 2
    assert memory.clear_tag(memory.TAG_JOKE) == 2
finally:
    memory._BOOK = None
    if book is not None:
        book.con.close()
    shutil.rmtree(tmp, ignore_errors=True)

print('ok')
