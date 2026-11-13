"""Train Yumi's tiny intent mind and export it as ONNX: the local «System One».

Reads the sayings (data/intents-corpus.json, made by hand and by a big mind)
plus the user's own actions.json, teaches rubert-tiny2 to pick the right button
and exports a quantized model into ~/.local/share/yumi/intents/. There yumi/intents.py
picks it up: a typed answer with a probability, no text generation, no parsing.

Run inside the training venv (tools/intents_setup.sh makes it):
    ~/.local/share/yumi/intents-train/bin/python tools/intents_train.py

Flags: --out DIR, --corpus PATH, --actions PATH, --base MODEL, --epochs N, --seed N
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))       # so the training canon equals the runtime canon

HOME_MODEL = Path.home() / '.local/share/yumi/intents'
CHAT = 'chat'                       # the label that means «none of the buttons: talk to me»
MIN_PER_LABEL = 6                   # fewer sayings than this: templates fill the gap
MAX_LEN = 32                        # a spoken phrase is short; longer is not speech
TEMPLATES_RU = ('открой {}', 'запусти {}', 'открой пожалуйста {}', '{} давай',
                'дай {}', 'хочу {}', 'открой {} пожалуйста', '{}')
TEMPLATES_EN = ('open {}', 'open the {}', 'run {}', 'start {}', '{} please',
                'give me the {}', 'i want the {}', '{}')


def read_corpus(path):
    """(label, [phrases]) out of the corpus file; the doc keys are skipped"""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    out = []
    for it in data['intents']:
        phrases = [str(p).strip() for p in it.get('phrases') or [] if str(p).strip()]
        if phrases:
            out.append((str(it['label']), phrases))
    return out


def read_actions(path):
    """the user's own buttons (actions.json): their sayings become training data too"""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for a in data:
        if isinstance(a, dict) and a.get('command'):
            label = str(a.get('label') or a['command'])
            sayings = [str(p).strip() for p in (a.get('phrases') or []) + (a.get('keywords') or [])
                       if str(p).strip()]
            if sayings:
                out.append((label, sayings))
    return out


def padded(label, phrases):
    """a label with too few sayings gets template clothes: «открой X», «open the X»…
    without it a label of three phrases is a coin toss, not a class"""
    if len(phrases) >= MIN_PER_LABEL:
        return list(phrases)
    words = [w for p in phrases for w in p.split() if len(w) >= 3] or [label]
    fill = []
    for w in words[:4]:
        cyr = any('\u0400' <= c <= '\u04FF' for c in w)
        for t in (TEMPLATES_RU if cyr else TEMPLATES_EN):
            fill.append(t.format(w))
    return list(phrases) + fill


def canon(text):
    """the words of a phrase the way yumi/intents.py reads them at runtime:
    the exact same normalization, or the model is taught on one alphabet and
    answers in another"""
    from yumi import nlu
    return ' '.join(nlu.normalize(text))


def examples(paths, skip_query=False):
    """[(text, label)] for training: the corpus first, then the user's actions;
    a button already in the corpus is not taught twice. skip_query leaves out the
    url buttons that want a {query}: the classifier cannot cut the query out
    (nlu.py _slots does), so it does not answer for them yet"""
    out, seen = [], set()
    taken = set()
    for path in paths:
        for label, phrases in read_actions(path) if str(path).endswith('actions.json') \
                else read_corpus(path):
            if label in taken:
                continue
            taken.add(label)
            for p in padded(label, phrases):
                p = canon(p)
                if p and p not in seen:
                    seen.add(p)
                    out.append((p, label))
    return out


def split(data, seed):
    """train/val: every 5th phrase of a label goes to the val pile (at least two
    when the label has enough); the val pile must hold every label, or the report lies"""
    rng = random.Random(seed)
    by_label = {}
    for i, (text, label) in enumerate(data):
        by_label.setdefault(label, []).append(i)
    val = set()
    for idx in by_label.values():
        rng.shuffle(idx)
        n_val = max(2, len(idx) // 5) if len(idx) >= MIN_PER_LABEL else min(1, len(idx))
        val.update(idx[:n_val])
    train = [data[i] for i in range(len(data)) if i not in val]
    vald = [data[i] for i in sorted(val)]
    return train, vald


def train(train_set, val_set, labels, base, epochs, seed):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.manual_seed(seed)
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(
        base, num_labels=len(labels))
    index = {label: i for i, label in enumerate(labels)}

    def batch(data):
        text = [t for t, _ in data]
        gold = torch.tensor([index[l] for _, l in data])
        enc = tok(text, padding=True, truncation=True, max_length=MAX_LEN,
                  return_tensors='pt')
        return enc, gold

    train_enc, train_gold = batch(train_set)
    val_enc, val_gold = batch(val_set)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-5)
    model.train()
    for epoch in range(epochs):
        perm = torch.randperm(len(train_gold))
        losses = []
        for i in range(0, len(perm), 8):
            idx = perm[i:i + 8]
            out = model(input_ids=train_enc['input_ids'][idx],
                        attention_mask=train_enc['attention_mask'][idx])
            loss = torch.nn.functional.cross_entropy(out.logits, train_gold[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            hit = (model(**val_enc).logits.argmax(-1) == val_gold).float().mean().item()
        model.train()
        print(f'epoch {epoch + 1}/{epochs}: loss {sum(losses) / len(losses):.4f}, '
              f'val accuracy {hit:.3f}')
    return model, tok


def report(model, tok, val_set, labels):
    """per-label honesty: a 90% overall can hide a label that never wins"""
    import torch
    index = {label: i for i, label in enumerate(labels)}
    text = [t for t, _ in val_set]
    gold = [index[l] for _, l in val_set]
    with torch.no_grad():
        pred = model(**tok(text, padding=True, truncation=True,
                           max_length=MAX_LEN, return_tensors='pt')).logits.argmax(-1).tolist()
    print('\nper label (val):')
    for i, label in enumerate(labels):
        gold_idx = [g for g in gold if g == i]
        if not gold_idx:
            continue
        good = sum(1 for p, g in zip(pred, gold) if g == i and p == i)
        print(f'  {label:14s} {good}/{len(gold_idx)}')
    wrong = [(val_set[j][0], labels[gold[j]], labels[pred[j]])
             for j, (p, g) in enumerate(zip(pred, gold)) if p != g]
    if wrong:
        print('misses:')
        for t, want, got in wrong[:12]:
            print(f'  «{t}»: want {want}, got {got}')


def export(model, tok, labels, out_dir, base):
    """fp32 → ONNX → int8 (dynamic quantization): ~110 MB becomes ~30 MB"""
    import torch

    model.eval()
    out_dir.mkdir(parents=True, exist_ok=True)
    fp32 = out_dir / 'model.fp32.onnx'
    enc = tok('открой терминал', return_tensors='pt', max_length=MAX_LEN)
    args = (enc['input_ids'], enc['attention_mask'])
    try:
        torch.onnx.export(model, args, str(fp32), input_names=['input_ids', 'attention_mask'],
                          output_names=['logits'],
                          dynamic_axes={'input_ids': {0: 'batch', 1: 'seq'},
                                        'attention_mask': {0: 'batch', 1: 'seq'},
                                        'logits': {0: 'batch'}},
                          opset_version=17, dynamo=False)
    except TypeError:                      # older torch: no dynamo switch at all
        torch.onnx.export(model, args, str(fp32), input_names=['input_ids', 'attention_mask'],
                          output_names=['logits'],
                          dynamic_axes={'input_ids': {0: 'batch', 1: 'seq'},
                                        'attention_mask': {0: 'batch', 1: 'seq'},
                                        'logits': {0: 'batch'}},
                          opset_version=17)
    onnx_out = out_dir / 'model.onnx'
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        quantize_dynamic(str(fp32), str(onnx_out), weight_type=QuantType.QInt8)
        fp32.unlink()
    except Exception as e:                 # quantization is a kindness, not a need
        print(f'quantization skipped ({e}); the fp32 model stays')
        fp32.replace(onnx_out)
    tok.save_pretrained(out_dir)
    (out_dir / 'labels.json').write_text(
        json.dumps(labels, ensure_ascii=False, indent=1), encoding='utf-8')
    (out_dir / 'meta.json').write_text(json.dumps({
        'base': base, 'trained': time.strftime('%Y-%m-%d %H:%M'),
        'max_len': MAX_LEN, 'labels': labels}, ensure_ascii=False, indent=1),
        encoding='utf-8')


def probe(out_dir, labels):
    """a look into the export: the phrases the rules already know, and the ones
    they do not; runs on the fresh onnx file with plain onnxruntime"""
    try:
        import numpy as np
        import onnxruntime as ort
        from transformers import AutoTokenizer
    except ImportError:
        print('(no onnxruntime in the training venv: probe skipped)')
        return
    tok = AutoTokenizer.from_pretrained(out_dir)
    sess = ort.InferenceSession(str(Path(out_dir) / 'model.onnx'))
    for text in ('открой терминал', 'запусти консольку пожалуйста', 'врубь музыку',
                 'поставь на паузу', 'открой блокнот', 'какая сегодня погода',
                 'расскажи анекдот', 'привет'):
        enc = tok(text, return_tensors='np', max_length=MAX_LEN,
                  padding=True, truncation=True)
        logits = sess.run(None, {'input_ids': enc['input_ids'].astype(np.int64),
                                 'attention_mask': enc['attention_mask'].astype(np.int64)})[0][0]
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        best = int(probs.argmax())
        print(f'  {text!r:38s} → {labels[best]}  {probs[best]:.2f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default=str(HERE / 'data/intents-corpus.json'))
    ap.add_argument('--actions', default='~/.config/yumi/actions.json')
    ap.add_argument('--out', default=str(HOME_MODEL))
    ap.add_argument('--base', default='cointegrated/rubert-tiny2')
    ap.add_argument('--epochs', type=int, default=12)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    from transformers import AutoModelForSequenceClassification  # noqa: F401  (checks the venv)

    data = examples([args.corpus, args.actions])
    labels = sorted({label for _, label in data} | {CHAT})
    for label in labels:
        n = sum(1 for _, l in data if l == label)
        print(f'{label:14s} {n:4d} phrases')
    if len(labels) < 3:
        sys.exit('too few labels: the corpus is empty?')
    train_set, val_set = split(data, args.seed)
    print(f'\n{len(train_set)} train, {len(val_set)} val, {len(labels)} labels; '
          f'base {args.base}')
    model, tok = train(train_set, val_set, labels, args.base, args.epochs, args.seed)
    print()
    report(model, tok, val_set, labels)
    out = Path(args.out).expanduser()
    export(model, tok, labels, out, args.base)
    size = (out / 'model.onnx').stat().st_size / 1e6
    print(f'\nexported: {out}/model.onnx ({size:.1f} MB), labels.json, tokenizer')
    probe(out, labels)


if __name__ == '__main__':
    main()
