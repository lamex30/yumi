#!/usr/bin/env python3
"""What the wake's diary says about itself: read /tmp/yumi/wake.jsonl (the
doubts' book the stt daemon keeps) and count what happened — the candidates,
the moments sent to the judge, the confirmations, the rejections, the stops,
the wakes and the latency of each wake.

Without a labeled set the tool prints raw counts only: recall, precision, FAR
and FRR are never guessed as percentages — they appear only when the diary's
lines carry a 'truth' field (True when the person really called the name, False
when they did not), which an offline test session records.

Run: python3 tools/wake_metrics.py [path/to/wake.jsonl]
"""
import json
import sys
from pathlib import Path

TAP_DIR = Path('/tmp/yumi')


def load(path):
    try:
        raw = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError as e:
        print(f'no diary at {path}: {e}')
        return []
    out = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def main():
    path = sys.argv[1] if sys.argv[1:] else TAP_DIR / 'wake.jsonl'
    lines = load(path)
    if not lines:
        print('nothing counted: the diary is empty or absent')
        return
    c = {'candidates': 0, 'sent': 0, 'held': 0, 'confirmed': 0, 'rejected': 0,
         'ignored': 0, 'ear_ignored': 0, 'wakes': 0, 'stops': 0,
         'latencies': [], 'labeled': 0, 'truth_pos': 0, 'truth_neg': 0,
         'tp': 0, 'fp': 0, 'fn': 0, 'tn': 0}
    by_engine = {}          # engine -> the same truth counts (compare_wake labels
                            # every line with the engine it came from)
    for line in lines:
        arb = line.get('arbitration')
        if line.get('wake_candidate'):
            c['candidates'] += 1
        if arb == 'SEND_JUDGE':
            if line.get('judge_called'):
                c['sent'] += 1
            else:
                c['held'] += 1
        elif arb == 'JUDGE_RESULT':
            c['confirmed' if line.get('judge_result') == 'confirmed' else 'rejected'] += 1
        elif arb == 'IGNORE':
            c['ignored'] += 1
            if line.get('ear_veto'):
                c['ear_ignored'] += 1
        elif arb == 'WAKE':
            c['wakes'] += 1
            if line.get('latency_ms') is not None:
                c['latencies'].append(line['latency_ms'])
        elif arb == 'STOP':
            c['stops'] += 1
        if isinstance(line.get('truth'), bool):
            c['labeled'] += 1
            truth = line['truth']
            woke = bool(line.get('final_wake'))
            c['truth_pos' if truth else 'truth_neg'] += 1
            if truth and woke:
                c['tp'] += 1
            elif truth and not woke:
                c['fn'] += 1
            elif not truth and woke:
                c['fp'] += 1
            else:
                c['tn'] += 1
            eng = line.get('engine')
            if eng:
                e = by_engine.setdefault(eng, {'pos': 0, 'neg': 0, 'tp': 0, 'fp': 0,
                                               'fn': 0, 'tn': 0})
                e['pos' if truth else 'neg'] += 1
                if truth and woke:
                    e['tp'] += 1
                elif truth and not woke:
                    e['fn'] += 1
                elif not truth and woke:
                    e['fp'] += 1
                else:
                    e['tn'] += 1

    print(f'diary: {path} ({len(lines)} moments)')
    for key in ('candidates', 'sent', 'held', 'confirmed', 'rejected', 'ignored',
                'ear_ignored', 'wakes', 'stops'):
        print(f'  {key}: {c[key]}')
    if c['latencies']:
        mean = sum(c['latencies']) / len(c['latencies'])
        print(f'  mean wake latency: {mean:.0f} ms over {len(c["latencies"])} wakes')
    judge_share = c['confirmed'] / c['sent'] if c['sent'] else None
    if judge_share is not None:
        print(f'  judge confirmations: {judge_share:.0%} of {c["sent"]} reads')
    if c['labeled']:
        # a labeled session: now the rates may be spoken of as rates
        pos, neg = c['truth_pos'], c['truth_neg']
        print('  labeled session:')
        if pos:
            print(f'    recall: {c["tp"] / pos:.0%}  false reject rate: {c["fn"] / pos:.0%}')
        if neg:
            print(f'    precision: {c["tp"] / (c["tp"] + c["fp"]):.0%}'
                  f'  false accept rate: {c["fp"] / neg:.0%}')
        print(f'    tp={c["tp"]} fp={c["fp"]} fn={c["fn"]} tn={c["tn"]}')
    else:
        print('  no truth labels in the diary: the rates of recall, precision, FAR'
              ' and FRR are not guessed — label a test session (truth: true/false'
              ' in the lines) to get them')
    if len(by_engine) > 1:
        # compare_wake's diary: the same recordings through different engines
        print('  by engine:')
        for eng, e in sorted(by_engine.items()):
            row = [f'{eng}:']
            if e['pos']:
                row.append(f"recall {e['tp'] / e['pos']:.0%} (tp={e['tp']} fn={e['fn']})")
            if e['neg']:
                row.append(f"FAR {e['fp'] / e['neg']:.0%} (fp={e['fp']} tn={e['tn']})")
            if e['tp'] + e['fp']:
                row.append(f"precision {e['tp'] / (e['tp'] + e['fp']):.0%}")
            print('    ' + '  '.join(row))


if __name__ == '__main__':
    main()
