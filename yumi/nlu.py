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

An entry may carry, instead of a command, a "url" with {query} in it: the words
said beside the trigger fill it in, and the browser opens at the address
(«найди в интернете котиков» → yandex.ru/search/?text=котиков); "speak" may
carry {query} too («Ищу котиков»).
"""
import re
from dataclasses import dataclass, field

from . import entities
from .stt_daemon import near

SCORE_PHRASE = 1.0        # the phrase said whole
SCORE_KEYWORD = 0.8       # one of the keywords heard
SCORE_FUZZY = 0.6         # the same, one bent letter here and there
MIN_SCORE = SCORE_FUZZY   # the line under which an intent is no intent

NEGATERS = frozenset(('не', 'not', "don't", 'dont', 'никогда', 'never'))


@dataclass
class IntentResult:
    """what a phrase meant: the button, how sure the match was, the words cut
    out for a search, the built-in entities found in it and the also-rans.
    match() still hands out the old (action, score, slots) triple; the skills
    that want the rest call recognize() instead."""
    action: dict | None
    confidence: float
    slots: dict
    entities: dict = field(default_factory=dict)
    alternatives: list = field(default_factory=list)
    intent: str = ''

    def as_tuple(self):
        """the old contract: (action, score, slots)"""
        return self.action, self.confidence, self.slots


def _intent_name(action):
    """a stable name for the log and the panel: the button's id, or its command"""
    ident = str(action.get('id') or '').strip()
    if ident:
        return ident[:64]
    return 'custom:' + str(action.get('url') or action.get('command') or '')[:64]


def _as_int(value):
    """a button's priority is a small whole number; a bad one counts as zero"""
    try:
        return max(-100, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0

# the polite words and the search verbs that are neither the trigger nor the
# subject of a search: they go when the query is cut out of the phrase
# (url entries with {query}), wherever they stand in the phrase
QUERY_FILLERS = frozenset((
    'в', 'во', 'на', 'по', 'ну', 'а', 'и', 'про', 'об', 'обо', 'давай',
    'пожалуйста', 'please', 'the', 'a', 'an', 'for', 'about',
    'найди', 'найти', 'поищи', 'поищите', 'поискать', 'найдите', 'мне', 'нам',
    'search', 'find', 'look', 'me', 'us'))

# a url entry fired by a keyword alone wants one of these in the phrase too:
# «что происходит в интернете» is somebody talking, not a wish to search;
# a phrase trigger («найди в интернете») carries its verb and needs no more
SEARCH_VERB = ('найд', 'поищ', 'поиск', 'погугл', 'search', 'find', 'googl', 'look')

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
    """where the phrase sits whole inside the words, letter for letter: a
    contiguous run where every word of it is the word said there (any of its
    forms); the start index, or None"""
    n = len(p)
    for i in range(len(forms_list) - n + 1):
        if all(p[j] in forms_list[i + j] for j in range(n)):
            return i
    return None


def _seq_bent(p, toks):
    """where the phrase sits whole inside the words, with the ears' slips
    forgiven: a contiguous run where every word is one bent letter from the
    word said there; the start index, or None"""
    n = len(p)
    for i in range(len(toks) - n + 1):
        if all(_bent(p[j], toks[i + j]) for j in range(n)):
            return i
    return None


def _score(entry, toks, forms_list):
    """the best score this entry earns on the phrase (None under MIN_SCORE)"""
    best = None
    for p in _lists(entry.get('phrases')):
        if _seq(p, forms_list) is not None:
            return SCORE_PHRASE          # a sure win: nothing later can beat it
        if _seq_bent(p, toks) is not None:
            best = max(best or 0, SCORE_FUZZY)
    words = _lists(entry.get('keywords'))
    if words:
        if any(all(any(w in fl for fl in forms_list) for w in k) for k in words):
            best = max(best or 0, SCORE_KEYWORD)      # any one keyword is enough
        elif any(all(any(_bent(w, t) for t in toks) for w in k) for k in words):
            best = max(best or 0, SCORE_FUZZY)
    return best


def _slots(entry, text, toks, forms_list):
    """{'query': 'котиков'} for an entry whose url carries {query}: the words
    that are neither the trigger («найди в интернете») nor the polite words
    around it — wherever they stand in the phrase («поищи котиков в интернете»
    works too), as they were said: with ё and the case kept, a search wants
    real words; {} when the url wants no query"""
    if '{query}' not in str(entry.get('url') or ''):
        return {}
    raw = [w.strip('.,!?"«»…:;()[]-—') for w in str(text or '').split()]
    drop = set()
    for p in _lists(entry.get('phrases')):
        i = _seq(p, forms_list)
        if i is not None:
            drop.update(range(i, i + len(p)))
    if not drop:                         # the keywords fired: they are the trigger
        for k in _lists(entry.get('keywords')):
            for w in k:
                for j, fl in enumerate(forms_list):
                    if w in fl or _bent(w, toks[j]):
                        drop.add(j)
                        break
    query = []
    for j, w in enumerate(raw):
        nt = normalize(w)
        if not nt or j in drop or nt[0] in QUERY_FILLERS:
            continue
        query.append(w)
    return {'query': ' '.join(query)}


def recognize(text, actions, use_intents=True, min_confidence=None):
    """the best intent as an IntentResult, or None when nothing earns its keep.
    The rules keep their old order: a whole phrase (1.00) beats a keyword
    (0.80), which beats the ears' bent word (0.60); a tie goes to the button
    written first. `min_confidence` is the line from the settings: under it the
    phrase is left to the chat, even when a keyword was heard.

    When the rules end with nothing, the tiny mind (yumi/intents.py, if it was
    trained: tools/intents_setup.sh) gets one look at the phrase; it answers
    with a probability, and only a sure answer counts. Its own threshold is
    stricter, and the panel's line applies to it too."""
    toks = normalize(text)
    if not toks or any(t in NEGATERS for t in toks):
        return None
    forms_list = [forms(t) for t in toks]
    hits = []
    for a in actions or []:
        if not isinstance(a, dict) or not (a.get('command') or a.get('url')):
            continue
        score = _score(a, toks, forms_list)
        if score is not None:
            hits.append((a, score))
    if not hits:
        if not use_intents:
            return None
        from . import intents                 # lazy: the classifier reads nlu.normalize
        hit = intents.classify(text, actions)
        if not hit:
            return None
        action, score, slots = hit
        if min_confidence is not None and score < min_confidence:
            return None
        return IntentResult(action, score, slots, entities.extract(text),
                            [], _intent_name(action))

    def rank(hit):
        """the score first; `priority` from a button only breaks a tie, so a
        louder keyword never pushes out a phrase said whole"""
        action, score = hit
        return score, _as_int(action.get('priority'))

    best = hits[0]
    for hit in hits[1:]:
        if rank(hit) > rank(best):           # strictly greater: the first wins a tie
            best = hit
    if min_confidence is not None and best[1] < min_confidence:
        return None
    slots = _slots(best[0], text, toks, forms_list)
    if '{query}' in str(best[0].get('url') or ''):
        if not slots.get('query'):
            return None                      # no subject in the phrase: not a search
        if best[1] != SCORE_PHRASE and not any(
                t.startswith(SEARCH_VERB) for t in toks):
            return None                      # a keyword alone is somebody talking
    alternatives = [(a, score) for a, score in sorted(hits, key=lambda h: -h[1])
                    if a is not best[0]][:3]
    return IntentResult(best[0], best[1], slots, entities.extract(text),
                        alternatives, _intent_name(best[0]))


def match(text, actions, use_intents=True, min_confidence=None):
    """(action, score, slots) of the best intent for the phrase, or None when
    nothing earns its keep — the contract the button runner has always used.
    See recognize() for the same answer with entities and alternatives."""
    hit = recognize(text, actions, use_intents=use_intents,
                    min_confidence=min_confidence)
    return hit.as_tuple() if hit else None
