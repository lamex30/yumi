"""The tiny mind for the voice intents: a local ONNX classifier, the «System One»
behind the rules of nlu.py.

When the sayings in actions.json did not match (no phrase, no keyword, nothing
bent), this file picks the closest button the way a trained model does: the text
goes in, a typed answer with a probability comes out — no generation, nothing
to parse. Trained by tools/intents_setup.sh from data/intents-corpus.json plus
the user's own actions.json; the model lives in ~/.local/share/yumi/intents
(onnxruntime only: the wordpiece below reads the vocabulary straight from
tokenizer.json, no tokenizers library, no transformers, no torch).

 classify() returns None quietly when anything is missing (no model, no
onnxruntime, the phrase has a «не» in it) — the app then behaves exactly as
before, the chat takes the phrase. The single try (like the lemmatizer in
nlu.py) keeps the miss cheap.
"""
import json
import os
from pathlib import Path

HOME = Path(os.environ.get('YUMI_INTENTS_HOME') or Path.home() / '.local/share/yumi/intents')
MIN_PROB = 0.85           # under this the answer is «not mine»: the chat takes the phrase
COS_MIN = 0.90            # how close the phrase must sit to its class's center: softmax
                          # alone is too brave on the phrases it never saw («открой
                          # окно» scores 0.95 for the terminal), the cosine is not
MAX_LEN = 32              # tokens per phrase, the same the training kept
NEGATERS = frozenset(('не', 'not', "don't", 'dont', 'никогда', 'never'))

_mind = None              # (session, labels, cents, vocab, cls, sep): loaded once
_tried = False


def _canon(text):
    """the phrase the way both the training and the runtime read it: the words
    alone, lower case, ё folded into е (nlu.py keeps this rule, the model was
    taught on the same canon)"""
    from . import nlu
    return ' '.join(nlu.normalize(text))


def _mind_load():
    """(session, labels, cents, vocab, cls, sep) or None: tried once, remembered"""
    global _mind, _tried
    if not _tried:
        _tried = True
        try:
            import onnxruntime as ort

            labels = json.loads((HOME / 'labels.json').read_text(encoding='utf-8'))
            cents = json.loads((HOME / 'centroids.json').read_text(encoding='utf-8'))
            spec = json.loads((HOME / 'tokenizer.json').read_text(encoding='utf-8'))
            vocab = spec['model']['vocab']
            cls, sep = vocab['[CLS]'], vocab['[SEP]']
            sess = ort.InferenceSession(str(HOME / 'model.onnx'),
                                        providers=['CPUExecutionProvider'])
            _mind = (sess, labels, cents, vocab, cls, sep)
        except Exception:
            _mind = None          # no model, no deps, a broken file: silence
    return _mind


def forget():
    """the model changed on the disk (a retrain while she runs): look again"""
    global _mind, _tried
    _mind, _tried = None, False


def _pieces(word, vocab):
    """the word as wordpiece keeps it: the longest spellings the vocabulary
    knows, the tails in «##» clothes; a word it cannot dress whole turns into
    [UNK] — that is how bert was taught and the model with it"""
    if len(word) > 50:
        return ['[UNK]']
    out, start = [], 0
    while start < len(word):
        end = len(word)
        found = None
        while start < end:
            sub = word[start:end]
            if start:
                sub = '##' + sub
            if sub in vocab:
                found = sub
                break
            end -= 1
        if not found:
            return ['[UNK]']
        out.append(found)
        start = end
    return out


def _encode(phrase, vocab, cls, sep):
    """ids and the attention mask of a canon phrase: the words the bert way —
    punctuation apart, wordpiece within, [CLS] before, [SEP] after, cut at
    MAX_LEN so the exported model gets the length it was traced with"""
    words = []
    for w in phrase.split():
        buf = ''
        for ch in w:
            if ch.isalnum():
                buf += ch
            else:
                if buf:
                    words.append(buf)
                buf = ''
                words.append(ch)
        if buf:
            words.append(buf)
    tokens = []
    for w in words:
        tokens.extend(_pieces(w, vocab))
        if len(tokens) >= MAX_LEN - 2:
            break
    tokens = tokens[:MAX_LEN - 2]
    ids = [cls] + [vocab[t] for t in tokens] + [sep]
    return ids, [1] * len(ids)


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
    sess, labels, cents, vocab, cls, sep = mind
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
    ids, mask = _encode(phrase, vocab, cls, sep)
    logits, emb = sess.run(None, {'input_ids': [ids], 'attention_mask': [mask]})
    logits, emb = logits[0], emb[0]
    exp = [2.718281828 ** (x - max(logits)) for x in logits]
    total = sum(exp)
    probs = [e / total for e in exp]    # softmax by hand: numpy would be another import
    best, prob = None, 0.0
    for a, idx in offered:
        if probs[idx] > prob:
            best, prob = a, probs[idx]
    if best is None or prob < min_prob or (chat is not None and probs[chat] > prob):
        return None
    # the honesty check: the phrase must sit near its class's center, not merely
    # score high on it (softmax is brave, the cosine is not)
    norm = sum(x * x for x in emb) ** 0.5
    cent = cents[labels.index(str(best['label']))]
    cos = sum(x * y for x, y in zip(emb, cent)) / (norm * sum(x * x for x in cent) ** 0.5)
    if cos < COS_MIN:
        return None
    return best, prob, {}
