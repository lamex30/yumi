"""The tiny mind for the voice intents: a local ONNX classifier, the «System One»
behind the rules of nlu.py.

When the sayings in actions.json did not match (no phrase, no keyword, nothing
bent), this file picks the closest button the way a trained model does: the text
goes in, a typed answer with a probability comes out — no generation, nothing
to parse. Trained by tools/intents_setup.sh from data/intents-corpus.json plus
the user's own actions.json; the model lives in ~/.local/share/yumi/intents
(onnxruntime + tokenizers only: no torch, no transformers at runtime).

 classify() returns None quietly when anything is missing (no model, no
onnxruntime, no tokenizers, the phrase has a «не» in it) — the app then behaves
exactly as before, the chat takes the phrase. The single try (like the
lemmatizer in nlu.py) keeps the miss cheap.
"""
import json
import os
from pathlib import Path

HOME = Path(os.environ.get('YUMI_INTENTS_HOME') or Path.home() / '.local/share/yumi/intents')
MIN_PROB = 0.85           # under this the answer is «not mine»: the chat takes the phrase
NEGATERS = frozenset(('не', 'not', "don't", 'dont', 'никогда', 'never'))

_mind = None              # (session, labels, tokenizer): loaded once, kept for good
_tried = False


def _canon(text):
    """the phrase the way both the training and the runtime read it: the words
    alone, lower case, ё folded into е (nlu.py keeps this rule, the model was
    taught on the same canon)"""
    from . import nlu
    return ' '.join(nlu.normalize(text))


def _mind_load():
    """(session, labels, tokenizer) or None: tried once, then remembered"""
    global _mind, _tried
    if not _tried:
        _tried = True
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            labels = json.loads((HOME / 'labels.json').read_text(encoding='utf-8'))
            tok = Tokenizer.from_file(str(HOME / 'tokenizer.json'))
            tok.no_padding()
            tok.enable_truncation(max_length=32)
            sess = ort.InferenceSession(str(HOME / 'model.onnx'),
                                        providers=['CPUExecutionProvider'])
            _mind = (sess, labels, tok)
        except Exception:
            _mind = None          # no model, no deps, a broken file: silence
    return _mind


def forget():
    """the model changed on the disk (a retrain while she runs): look again"""
    global _mind, _tried
    _mind, _tried = None, False


def classify(text, actions, min_prob=MIN_PROB):
    """(action, score, {}) the way nlu.match returns its hits, or None: the best
    button for the phrase and how sure the mind is. `actions` are the entries
    actions.json gives; a button the model was not taught (its label is not in
    labels.json) and a url button with {query} (the slots are the rules' job,
    see nlu.py) are not offered to it. The chat label wins a tie on purpose:
    a wrong command runs, a wrong chat just talks"""
    mind = _mind_load()
    if not mind or not actions:
        return None
    sess, labels, tok = mind
    phrase = _canon(text)
    if not phrase or any(t in NEGATERS for t in phrase.split()):
        return None                     # «не открывай» is the opposite of a wish
    chat = labels.index('chat') if 'chat' in labels else None
    offered = []
    for a in actions:
        if not isinstance(a, dict) or not (a.get('command') or a.get('url')):
            continue
        if '{query}' in str(a.get('url') or ''):
            continue                    # a search wants the words cut out: the rules' craft
        if str(a.get('label')) in labels:
            offered.append((a, labels.index(str(a['label']))))
    if not offered:
        return None
    enc = tok.encode(phrase)
    logits = sess.run(None, {
        'input_ids': [enc.ids], 'attention_mask': [enc.attention_mask]})[0][0]
    exp = [2.718281828 ** (x - max(logits)) for x in logits]
    total = sum(exp)
    probs = [e / total for e in exp]    # softmax by hand: numpy would be another import
    best, prob = None, 0.0
    for a, idx in offered:
        if probs[idx] > prob:
            best, prob = a, probs[idx]
    if best is None or prob < min_prob or (chat is not None and probs[chat] > prob):
        return None
    return best, prob, {}
