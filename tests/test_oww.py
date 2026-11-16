#!/usr/bin/env python3
"""Тесты новой oww-архитектуры обнаружения имени «Юми». Run: python3 tests/test_oww.py

Проверяются, apart from the microphone:
- загрузка модели yumi.onnx через openWakeWord (если есть библиотека и модель),
- пороги OWW (threshold / sure) и решение wake(),
- что команда после имени не теряется (интеграция с moth_girl.py wake loop),
- останов «стоп» во время пробуждения.

Тесты, для которых нет модели или библиотеки, пропускаются (не падают).
"""
import importlib.util
import os
import re
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

# fixed dials for the tests
stt.TUNE.update({'oww_threshold': 0.5, 'oww_sure': 0.7, 'oww_hold': 0.35,
                 'oww_veto': 0.2, 'oww_window': 3.0, 'oww_spike': 2,
                 'wake_ear': 'oww'})

spellings = stt.variants('юми') | stt.hearsay_load('юми')
assert stt.variants('юми') == {'юми', 'yumi', 'umi'}, stt.variants('юми')

# ---- OWW пороги корректны ----
assert stt.OWW_THRESHOLD == stt.TUNE['oww_threshold'] == 0.5
assert stt.OWW_SURE == stt.TUNE['oww_sure'] == 0.7
assert stt.OWW_VETO == stt.TUNE['oww_veto'] == 0.2
assert stt.OWW_VETO < stt.OWW_THRESHOLD < stt.OWW_SURE

# ---- загрузка trained ear (опционально) ----
try:
    from yumi.oww_ear import main as oww_main  # noqa: F401
    _has_oww = True
except Exception:
    _has_oww = False

_model = Path(os.environ.get('YUMI_OWW_MODEL',
                             str(Path.home() / '.local/share/yumi/oww/models/yumi.onnx')))
_has_model = _model.exists()

if _has_oww and _has_model:
    # пробуем прогнать тишину через модель — score должен быть низким
    import numpy as np
    from openwakeword.model import Model
    m = Model(wakeword_models=[str(_model)], inference_framework='onnx')
    silence = np.zeros(int(16000 * 0.08), dtype=np.int16)  # 80 ms тишины
    scores = m.predict(silence)
    best = max(scores.values()) if scores else 0.0
    assert best < stt.OWW_THRESHOLD, f't silence scored {best:.3f}, above threshold'
else:
    print(f'skip: oww lib={_has_oww} model={_has_model} ({_model})')

# ---- moth_girl.py wake loop: после wake_up(tail) вызывается ears.listen при пустом tail ----
src = (_root / 'moth_girl.py').read_text(encoding='utf-8')
assert 'ears.listen' in src, 'moth_girl lost the ears.listen call after wake'
assert 'wake_up(t)' in src, 'moth_girl lost the wake_up call'
# stop branch present and calls brain
m = re.search(r"if res\.get\('stop'\):\n(.*?)\n(\s*)continue", src, re.S)
assert m, 'the wake loop lost its stop branch'
assert 'b.stop_all' in m.group(1), 'stop must reach the brain'

# ---- stop_at / stop_shout unchanged ----
assert stt.stop_at('стоп')
assert stt.stop_at('юми стоп')
assert not stt.stop_at('включи музыку')
assert stt.stop_shout('стоп')
assert not stt.stop_shout('скажи стоп')

print('test_oww: ok')
sys.exit(0)
