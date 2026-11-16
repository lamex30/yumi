#!/usr/bin/env python3
"""Сравнение старого (vosk+судья) и нового (oww-only) движков пробуждения на одних и тех же записях.

Записи берутся из /tmp/yumi (кольцо ушей) и dataset/ (размеченные clean/complex/noisy).
Каждой записи ставится метка truth: True — в записи есть обращение «юми», False — нет.
Результаты пишутся в wake.jsonl (старая и новая маркируются полем 'engine'), чтобы
затем tools/wake_metrics.py мог посчитать recall, precision, FAR, FRR.

Запуск:
    python3 tools/compare_wake.py [--dir /tmp/yumi] [--limit N]

Требуется:
    - ~/.local/share/yumi/oww/bin/python с openwakeword (для нового движка)
    - ~/.local/share/yumi/stt/bin/python с vosk (для старого движка, через старый демон)
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

OWW_PY = Path(os.environ.get('YUMI_OWW_PY') or str(Path.home() / '.local/share/yumi/oww/bin/python'))
OWW_MODEL = Path(os.environ.get('YUMI_OWW_MODEL') or str(Path.home() / '.local/share/yumi/oww/models/yumi.onnx'))

TAP_DIR = Path(os.environ.get('YUMI_TAP_DIR') or '/tmp/yumi')
OUT = TAP_DIR / 'wake.jsonl'

# ---------------------------------------------------------------------------
# разметка truth по имени файла / подкаталогу
# ---------------------------------------------------------------------------
def truth_for(path: Path) -> bool:
    """True когда в записи, по имени файла, есть обращение «юми»."""
    name = path.name.lower()
    if any(k in name for k in ('юми', 'yumi', 'umi', 'wake', 'positive', 'pos')):
        return True
    # dataset/yumi_clean/* — чистые голоса, считаем позитивом
    if path.parent.parent.name == 'yumi_clean':
        return True
    # dataset/yumi_complex / yumi_noisy — шум/видео без обращения: негатив
    if path.parent.parent.name in ('yumi_complex', 'yumi_noisy'):
        return False
    return False


def wav_ok(path: Path) -> bool:
    """16 kHz mono s16 wav — формат, который понимают и oww, и vosk."""
    try:
        with wave.open(str(path), 'rb') as w:
            return w.getframerate() == 16000 and w.getnchannels() == 1 \
                and w.getsampwidth() == 2
    except (wave.Error, OSError):
        return False


# ===========================================================================
# Новый движок: openWakeWord через oww_probe.py --wav
# ===========================================================================
def oww_score(path: Path, threshold: float = 0.5) -> float:
    """Максимальный score trained ear на записи (новый движок)."""
    if not OWW_PY.exists():
        print(f'skip oww: {OWW_PY} not found')
        return None
    if not OWW_MODEL.exists():
        print(f'skip oww: model {OWW_MODEL} not found')
        return None
    try:
        out = subprocess.run(
            [str(OWW_PY), str(_root / 'tools' / 'oww_probe.py'),
             '--models', str(OWW_MODEL), '--wav', str(path),
             '--threshold', str(threshold)],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f'oww timeout on {path.name}')
        return None
    best = 0.0
    for line in out.stdout.splitlines():
        if '(' in line and ')' in line:
            try:
                score = float(line.rsplit('(', 1)[1].rstrip(')'))
                best = max(best, score)
            except ValueError:
                pass
    return best


# ===========================================================================
# Старый движок: vosk (старым KaldiRecognizer) напрямую на wav
# ===========================================================================
def vosk_text(path: Path) -> str:
    """Распознавание текста vosk small-ru на записи (старый движок)."""
    stt_py = Path(os.environ.get('YUMI_STT_PY') or str(Path.home() / '.local/share/yumi/stt/bin/python'))
    model_dir = Path(os.environ.get('YUMI_VOSK_MODEL') or str(Path.home() / '.local/share/yumi/stt/vosk-model-small-ru-0.22'))
    if not stt_py.exists():
        print(f'skip vosk: {stt_py} not found')
        return ''
    if not model_dir.exists():
        print(f'skip vosk: model {model_dir} not found')
        return ''
    code = f'''
from vosk import Model, KaldiRecognizer
import wave, json, sys
m = Model({model_dir!r})
w = wave.open({path!r}, 'rb')
rec = KaldiRecognizer(m, w.getframerate())
rec.SetWords(False)
out = []
while True:
    data = w.readframes(4000)
    if not data:
        break
    if rec.AcceptWaveform(data):
        r = json.loads(rec.Result())
        out.append(r.get('text', ''))
r = json.loads(rec.FinalResult())
out.append(r.get('text', ''))
print(' '.join(t for t in out if t))
'''
    try:
        res = subprocess.run([str(stt_py), '-c', code],
                             capture_output=True, text=True, timeout=120)
        return res.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f'vosk timeout on {path.name}')
        return ''


def vosk_wake(text: str) -> bool:
    """Есть ли в vosk-чтении обращение к имени (старый движок)."""
    if not text:
        return False
    t = text.lower().replace('ё', 'е')
    for tok in t.split():
        tok = tok.strip('.,!?«»…:;()[]-—')
        if tok in ('юми', 'юме', 'юмий', 'юмию', 'юми)', 'yumi', 'umi'):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description='compare old vs new wake engine')
    ap.add_argument('--dir', default=str(TAP_DIR),
                    help='directory with wav recordings (default /tmp/yumi)')
    ap.add_argument('--limit', type=int, default=0,
                    help='max number of recordings (0 = all)')
    ap.add_argument('--dataset', action='store_true',
                    help='also include dataset/ recordings')
    args = ap.parse_args()

    src = Path(args.dir)
    files = []
    if src.exists():
        files += sorted(p for p in src.glob('*.wav') if wav_ok(p))
    if args.dataset:
        for d in ('yumi_clean', 'yumi_complex', 'yumi_noisy'):
            dd = _root / 'dataset' / d
            if dd.exists():
                files += sorted(p for p in dd.rglob('*.wav') if wav_ok(p))
    if args.limit:
        files = files[:args.limit]

    if not files:
        print('no wav recordings found')
        return

    lines = []
    for path in files:
        truth = truth_for(path)
        new_score = oww_score(path)
        new_woke = new_score is not None and new_score >= 0.5
        old_text = vosk_text(path)
        old_woke = vosk_wake(old_text)

        line = {
            'file': str(path),
            'truth': truth,
            'engine': 'new',
            'wake_candidate': True,
            'final_wake': new_woke,
            'score': new_score,
            'threshold': 0.5,
            'latency_ms': 0,
        }
        lines.append(line)
        line2 = dict(line)
        line2.update(engine='old', final_wake=old_woke, text=old_text)
        lines.append(line2)

        print(f'{path.name:45s} truth={truth} new={new_woke} (score={new_score:.3f} if new_score else None) old={old_woke} text={old_text!r}')

    # append to the diary
    with open(OUT, 'a', encoding='utf-8') as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + '\n')

    # summary
    new_tp = sum(1 for l in lines if l['engine'] == 'new' and l['truth'] and l['final_wake'])
    new_fp = sum(1 for l in lines if l['engine'] == 'new' and not l['truth'] and l['final_wake'])
    new_fn = sum(1 for l in lines if l['engine'] == 'new' and l['truth'] and not l['final_wake'])
    old_tp = sum(1 for l in lines if l['engine'] == 'old' and l['truth'] and l['final_wake'])
    old_fp = sum(1 for l in lines if l['engine'] == 'old' and not l['truth'] and l['final_wake'])
    old_fn = sum(1 for l in lines if l['engine'] == 'old' and l['truth'] and not l['final_wake'])

    print(f'\n=== summary over {len(files)} recordings ===')
    print(f'new (oww):   tp={new_tp} fp={new_fp} fn={new_fn}')
    print(f'old (vosk):  tp={old_tp} fp={old_fp} fn={old_fn}')
    if new_tp + new_fn:
        print(f'new recall:  {new_tp / (new_tp + new_fn):.0%}')
    if old_tp + old_fn:
        print(f'old recall:  {old_tp / (old_tp + old_fn):.0%}')
    print(f'diary appended: {OUT} (+{len(lines)} lines)')


if __name__ == '__main__':
    main()