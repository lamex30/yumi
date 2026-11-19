#!/usr/bin/env python3
"""Her notes: the sqlite book, the vectors and the spoken wishes that reach it.
Run: python3 tests/test_notes.py"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import notes  # noqa: E402

# ---- the spoken wishes: the trigger goes, the rest is the note or the search
assert notes.voice_intent('запиши заметку купить молоко') == {
    'action': 'add', 'text': 'купить молоко'}
assert notes.voice_intent('Юми, запиши в заметки созвон с врачом') == {
    'action': 'add', 'text': 'созвон с врачом'}
assert notes.voice_intent('please, write a note: call mom') == {
    'action': 'add', 'text': 'call mom'}
assert notes.voice_intent('найди заметку про молоко') == {
    'action': 'search', 'query': 'молоко'}
assert notes.voice_intent('что в заметках про врача') == {
    'action': 'search', 'query': 'врача'}
assert notes.voice_intent('покажи заметки') == {'action': 'list'}
assert notes.voice_intent('show my notes') == {'action': 'list'}
assert notes.voice_intent('покажи заметки про молоко') == {
    'action': 'search', 'query': 'молоко'}
assert notes.voice_intent('последняя заметка') == {'action': 'last'}
assert notes.voice_intent('покажи последнюю заметку') == {'action': 'last'}
assert notes.voice_intent('что в последней заметке') == {'action': 'last'}
assert notes.voice_intent('what is my last note') == {'action': 'last'}
assert notes.voice_intent('last note') == {'action': 'last'}
# the ears bend the endings and sometimes keep her name at the head
assert notes.voice_intent('последний заметка') == {'action': 'last'}
assert notes.voice_intent('юми последний заметка') == {'action': 'last'}
assert notes.voice_intent('юлии последняя заметка') == {'action': 'last'}
assert notes.voice_intent('последние заметки') == {'action': 'last'}
assert notes.voice_intent('last notes') == {'action': 'last'}
assert notes.voice_intent('как дела') is None
assert notes.voice_intent('это интересная заметка') is None, 'a mention is not a wish'

# a long note gives her first sentences when the mind cannot make a summary
assert notes.brief('Короткая заметка.') == 'Короткая заметка.'
long = 'а' * 200 + '. ' + 'б' * 200
short = notes.brief(long)
assert short.endswith('.') and len(short) <= 260, short

# ---- the book: a throwaway sqlite file, the embedder off so the hashed
# vector stands in (the same fallback the diary trusts after a failure)
tmp = Path(tempfile.mkdtemp(prefix='yumi-notes-'))
book = None
try:
    notes.STATE_DIR = tmp
    notes.DB_FILE = tmp / 'yumi.db'
    book = notes.Notes()
    book._fail_until = time.time() + 3600.0
    s = {'embed_model': 'nomic-embed-text'}

    milk = book.add('Купить молоко и хлеб', s, source='voice')
    doctor = book.add('Созвон с врачом в среду', s, title='Врач')
    story = book.add('Идея для рассказа про мотылька', s)
    assert milk['id'] and milk['title'] == 'Купить молоко и хлеб', milk
    assert milk['source'] == 'voice'
    assert doctor['title'] == 'Врач'
    assert book.count() == 3

    # both vectors are beside every note, not just the text
    dim, emb, bagemb = book.con.execute(
        'SELECT dim, emb, bagemb FROM notes WHERE id=?', (milk['id'],)).fetchone()
    assert dim and emb and bagemb, (dim, emb, bagemb)

    found = book.search('молоко', s)
    assert found and found[0]['id'] == milk['id'], found
    assert found[0]['score'] > 0
    found = book.search('врач', s)
    assert found and found[0]['id'] == doctor['id'], found
    assert all(n['id'] != story['id'] for n in book.search('молоко', s))

    # a note is found by its own words; a search with nothing close finds nothing
    assert book.search('молоко хлеб', s)[0]['id'] == milk['id']
    assert book.search('квантовая хромодинамика', s) == []

    # editing rewrites the text and the vectors, and the title stays unless given
    changed = book.update(doctor['id'], 'Созвон с врачом перенесли на четверг', s)
    assert changed['text'].startswith('Созвон') and changed['title'] == 'Врач'
    assert book.search('четверг', s)[0]['id'] == doctor['id']
    assert book.search('среда', s) == [] or book.search('среда', s)[0]['id'] != doctor['id']

    # the newest first for the panel
    recent = book.recent(2)
    assert len(recent) == 2, recent
    assert recent[0]['updated'] >= recent[1]['updated']

    # deleting one, then clearing the book
    assert book.delete(milk['id']) is True and book.delete(milk['id']) is False
    assert book.count() == 2
    assert book.get(doctor['id'])['title'] == 'Врач'
    assert book.get(10_000) is None
    assert book.clear() == 2 and book.count() == 0
    assert book.recent() == []

    # an empty note is refused
    try:
        book.add('   ', s)
        raise AssertionError('an empty note was saved')
    except ValueError:
        pass

    # ---- the module-level face works (the shell and the panel call it this way)
    for name in ('add', 'update', 'delete', 'clear', 'count', 'recent', 'get', 'search'):
        assert callable(getattr(notes, name)), name
    notes._BOOK = book
    saved = notes.add('Заметка через модуль', s)
    assert notes.count() == 1 and notes.get(saved['id'])['text'] == 'Заметка через модуль'
    assert notes.search('через модуль', s)[0]['id'] == saved['id']
finally:
    notes._BOOK = None
    if book is not None:
        book.con.close()
    shutil.rmtree(tmp, ignore_errors=True)

print('ok')
