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
sys.path.insert(0, str(_root))          # so «from yumi.oww_ear import» finds the package
                                        # when the test runs as `python3 tests/test_oww.py`
spec = importlib.util.spec_from_file_location('stt_daemon', _root / 'yumi' / 'stt_daemon.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)

# fixed dials for the tests
stt.TUNE.update({'oww_threshold': 0.5, 'oww_sure': 0.7, 'oww_hold': 0.35,
                 'oww_veto': 0.2, 'oww_window': 3.0, 'oww_spike': 2,
                 'oww_cooldown': 2.0, 'wake_lead_ms': 1000.0, 'wake_listen': 15.0,
                 'wake_ear': 'oww'})

spellings = stt.variants('юми') | stt.hearsay_load('юми')
assert stt.variants('юми') == {'юми', 'yumi', 'umi'}, stt.variants('юми')

# ---- пороги OWW корректны ----
assert stt.OWW_THRESHOLD == stt.TUNE['oww_threshold'] == 0.5
assert stt.OWW_SURE == stt.TUNE['oww_sure'] == 0.7
assert stt.OWW_VETO == stt.TUNE['oww_veto'] == 0.2
assert stt.OWW_VETO < stt.OWW_THRESHOLD < stt.OWW_SURE
# ---- новые ручки из ТЗ (§6, §12): cooldown, лид фразы, окно прослушивания ----
assert stt.TUNE['oww_cooldown'] == stt.OWW_COOLDOWN
assert stt.TUNE['wake_lead_ms'] == stt.WAKE_LEAD_MS
assert stt.TUNE['wake_listen'] == stt.WAKE_LISTEN
# vosk-фолбэк: финалы стоп-ухи в окне вокруг пробуждения настраиваются и зажаты
assert stt.TUNE['wake_vosk_fallback'] == stt.WAKE_VOSK_FALLBACK
now = 1000.0
# команда, закончившаяся до имени, стоит (финал прямо на пробуждении)
assert stt.fresh_vosk([(now, 'включи музыку')], now) == 'включи музыку'
# первые слова после имени — команда; хвост позже окна — мимо
assert stt.fresh_vosk([(now + 2.0, 'включи музыку'),
                       (now + 7.0, 'вели и ревущий')], now) == 'включи музыку'
# болтовня задолго до пробуждения не считается командой
assert stt.fresh_vosk([(now - 10.0, 'привет как дела')], now) == ''
# пустой памяти — пустой фолбэк
assert stt.fresh_vosk([], now) == ''
# несколько финалов в окне идут вместе
assert stt.fresh_vosk([(now - 1.0, 'юми'), (now + 2.0, 'включи музыку')],
                      now) == 'юми включи музыку'

# ---- решение wake(): одиночный всплеск не будит, растяжка будит (§6) ----
now = 1000.0
stat = stt.EarStat()
# один кадр над порогом: кандида ещё не верят (spike=2)
assert not stt.oww_wake_decision(stat.feed(0.6, now))
# второй кадр подряд: верят, счёт выше порога — будит
assert stt.oww_wake_decision(stat.feed(0.6, now + 0.08))
# дип между слогами (ниже порога, но выше hold): вера держится — будит
assert stt.oww_wake_decision(stat.feed(0.4, now + 0.16))
# падение ниже hold: имя кончилось — решение ложь
assert not stt.oww_wake_decision(stat.feed(0.1, now + 0.24))
# неактивная статистика никогда не будит
assert not stt.oww_wake_decision({'active': False, 'cur': 0.9, 'hi': 0.9})
assert not stt.oww_wake_decision(None)

# ---- Utterance: лид больше LEAD сохраняется (голова фразы после имени, §7) ----
u = stt.Utterance()
u.lead = bytearray(b'\x01\x02' * 8000)      # 1 s on tape
u.lead_max = max(u.LEAD, len(u.lead))
u.feed(b'\x00\x00' * 100, False)            # a quiet chunk: the lead must survive
assert len(u.lead) >= 16000, 'the wake lead was trimmed by the quiet'


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

# ---- канал стопа и mute в wake(): разделы 8 и 9 ТЗ ----
src_stt = (_root / 'yumi' / 'stt_daemon.py').read_text(encoding='utf-8')
wake_src = src_stt.split('def wake(self, lang', 1)[1].split('def stop(self', 1)[0]
assert 'if muted():' in wake_src, 'the wake must honor the mute (her own voice on the air)'
assert 'stop_shout(words, spellings)' in wake_src, '«стоп» must cut through the mute'
assert 'transcribe_said' in wake_src, 'the phrase after the name must go to STT'
assert 'bytearray(ring[-lead:])' in wake_src, 'the phrase head must come off the ring'
assert 'elif ear_oww is None' in wake_src, 'vosk fallback: wake_ear vosk must not be deaf'
assert "if not woken or stop_shout(words, spellings)" in wake_src, \
    'already listening, «останови музыку» is a command, not a stop'


# ---- stop_at / stop_shout unchanged ----
assert stt.stop_at('стоп')
assert stt.stop_at('юми стоп')
assert not stt.stop_at('включи музыку')
assert stt.stop_shout('стоп')
assert not stt.stop_shout('скажи стоп')

print('test_oww: ok')
sys.exit(0)
