"""Voice intents: the sayings in actions.json answered without a mind.

Every button in ~/.config/yumi/actions.json may carry, beside its label and
command, the words that summon it by voice:

  [{"label": "Terminal", "command": "foot",
    "phrases": ["открой терминал", "запусти консоль", "open terminal"],
    "keywords": ["терминал", "консоль", "terminal"],
    "speak": "Открываю терминал"}]

The ears of the stt forgive a bent letter («терминай» for «терминал»), so the
matching goes in three steps and the first sure score counts:

  1.00 the whole phrase, letter for letter, inside the words
  0.80 one of the keywords is among the words (they are synonyms: «терминал»,
       «консоль» — any one is enough; a keyword of several words wants them all)
  0.60 the same, with the ears' slips forgiven (near())
A «не» / «not» in the phrase kills the match: «не открывай терминал» is the
opposite of a wish. pymorphy2, when it is installed, folds the word forms
together («терминалом» is «терминал»); without it the fuzzy matching is enough.
"""
import re

from .stt_daemon import near

SCORE_PHRASE = 1.0        # the phrase said whole
SCORE_KEYWORD = 0.8       # every keyword heard
SCORE_FUZZY = 0.6         # the same, one bent letter here and there
MIN_SCORE = SCORE_FUZZY   # the line under which an intent is no intent

NEGATERS = frozenset(('не', 'not', "don't", 'dont', 'никогда', 'never'))

_CYR = re.compile(r'[а-яё]')

_morph = None
_morph_tried = False


def _lemmatizer():
    """pymorphy2, tried once and only once (its dictionaries load slowly);
    None when it is not installed — the fuzzy matching carries on without it"""
    global _morph, _morph_tried
    if not _morph_tried:
        _morph_tried = True
        try:
            from pymorphy2 import MorphAnalyzer
            _morph = MorphAnalyzer()
        except Exception:
            _morph = None
    return _morph


def forms(word):
    """the spellings a word may be matched by: itself, and its normal form
    when pymorphy2 knows it («терминалом» → «терминал»); latin words stay as they are"""
    m = _lemmatizer()
    if m and _CYR.search(word):
        try:
            p = m.parse(word)
        except Exception:
            p = []
        if p:
            return {word, p[0].normal_form}
    return {word}


def normalize(text):
    """the words of a phrase the way the intents read them: lower case,
    punctuation gone, ё folded into е"""
    toks = [t.strip('.,!?"«»…:;()[]-—').lower().replace('ё', 'е')
            for t in str(text or '').split()]
    return [t for t in toks if t]


def _bent(a, b):
    """the ears' slip: one letter apart for the short words, two for the long
    ones (the same kindness the wake word gets)"""
    lim = 2 if min(len(a), len(b)) >= 6 else 1
    return near(a, b, lim) <= lim


def _lists(items):
    """phrases/keywords of an entry as normalized word lists, the empties gone"""
    out = []
    for it in items or []:
        toks = normalize(it)
        if toks:
            out.append(toks)
    return out


def _seq(p, forms_list):
    """the phrase sits whole inside the words, letter for letter: a contiguous
    run where every word of it is the word said there (any of its forms)"""
    n = len(p)
    for i in range(len(forms_list) - n + 1):
        if all(p[j] in forms_list[i + j] for j in range(n)):
            return True
    return False


def _seq_bent(p, toks):
    """the phrase sits whole inside the words, with the ears' slips forgiven:
    a contiguous run where every word is one bent letter from the word said there"""
    n = len(p)
    for i in range(len(toks) - n + 1):
        if all(_bent(p[j], toks[i + j]) for j in range(n)):
            return True
    return False


def _score(entry, toks, forms_list):
    """the best score this entry earns on the phrase (None under MIN_SCORE)"""
    best = None
    for p in _lists(entry.get('phrases')):
        if _seq(p, forms_list):
            return SCORE_PHRASE          # a sure win: nothing later can beat it
        if _seq_bent(p, toks):
            best = max(best or 0, SCORE_FUZZY)
    words = _lists(entry.get('keywords'))
    if words:
        if any(all(any(w in fl for fl in forms_list) for w in k) for k in words):
            best = max(best or 0, SCORE_KEYWORD)      # any one keyword is enough
        elif any(all(any(_bent(w, t) for t in toks) for w in k) for k in words):
            best = max(best or 0, SCORE_FUZZY)
    return best


def match(text, actions):
    """(action, score) of the best intent for the phrase, or None when nothing
    earns its keep; actions are the entries actions.json gives (those with
    phrases and keywords); a tie goes to the button written first"""
    toks = normalize(text)
    if not toks or any(t in NEGATERS for t in toks):
        return None
    forms_list = [forms(t) for t in toks]
    best = None
    for a in actions or []:
        if not isinstance(a, dict) or not a.get('command'):
            continue
        score = _score(a, toks, forms_list)
        if score is not None and (best is None or score > best[1]):
            best = (a, score)
    return best
