#!/usr/bin/env python3
"""Тесты новой oww-архитектуры обнаружения имени «Юми». Run: python3 tests/test_oww.py

Проверяются, apart from the microphone:
- загрузка модели yumi.onnx через openWakeWord (если есть библиотека и модель),
- пороги OWW (threshold / sure) и решение wake(),
- что команда после имени не теряется (интеграция с yumi/ui/senses.py wake loop),
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
# ---- быстрый путь: окно доходит до момента, когда спрашивающий стоит на конце
# фразы — фраза, произнесённая медленнее хвоста окна, читается целиком ----
assert stt.fresh_vosk([(now + 2.0, 'включи музыку'),
                       (now + 7.0, 'вели и ревущий')], now,
                      until=now + 7.0) == 'включи музыку вели и ревущий'
# ---- свежесть (VOSK_STALE): финал задолго до имени — чужая болтовня, и STT
# должен прочитать ленту вместо того, чтобы действовать по ней ----
assert stt.VOSK_STALE > 0
assert stt.fresh_vosk([(now - 3.5, 'привет как дела')], now,
                      until=now, slack=stt.VOSK_STALE) == ''
# свежий финал проходит — и до имени вплотную, и после него
assert stt.fresh_vosk([(now - 1.0, 'включи музыку')], now,
                      until=now, slack=stt.VOSK_STALE) == 'включи музыку'
assert stt.fresh_vosk([(now - 3.5, 'привет как дела'),
                       (now + 1.0, 'включи музыку')], now,
                      until=now + 1.0, slack=stt.VOSK_STALE) == \
    'привет как дела включи музыку'
# без slack окно прежнее: дневник alt не теряет старые финалы
assert stt.fresh_vosk([(now - 3.5, 'привет как дела')], now) == 'привет как дела'
assert stt.fresh_vosk([], now, until=now, slack=stt.VOSK_STALE) == ''

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

# ---- oww_ear_score: reset не оставляет read впереди fed (SUMMARY fed=read) ----
# раньше 'R' прибавлял строку к read, но не кадр к fed — после mute/перезапуска
# рекордера SUMMARY печатал read=fed+1
_r, _w = os.pipe()                          # the ear's answers (a real ear answers
os.write(_w, b'0.1000\n')                   # on its own pipe: this is that pipe)
_c, _cw = os.pipe()                         # the daemon's commands: a fake ear that


class _FakeEarProc:                         # never reads them (the real one does)
    def __init__(self):
        self.stdout = os.fdopen(_r, 'rb', 0)
        self.stdin = os.fdopen(_cw, 'wb', 0)

    def poll(self):
        return None                          # alive: the counters must count


ear = {'proc': _FakeEarProc(), 'buf': bytearray(), 'in': bytearray(),
       'dead': False, 'fed': 0, 'read': 0}
score = stt.oww_ear_score(ear, b'', reset=True)
assert ear['fed'] == 1 and ear['read'] == 1, \
    f"reset left fed={ear['fed']} read={ear['read']}: SUMMARY would show read=fed+1"
assert abs(score - 0.1) < 1e-6, f'reset answered {score}'
# the next frame: one written, one line back — both counters move together
os.write(_w, b'0.2000\n')
score = stt.oww_ear_score(ear, b'\x00' * stt.OWW_CHUNK)
assert ear['fed'] == 2 and ear['read'] == 2, \
    f"a frame left fed={ear['fed']} read={ear['read']}"
assert abs(score - 0.2) < 1e-6, f'the frame answered {score}'
ear['proc'].stdin.close()
ear['proc'].stdout.close()
os.close(_w)
os.close(_c)


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

# ---- yumi/ui/senses.py wake loop: после wake_up(tail) вызывается ears.listen при пустом tail ----
src = (_root / 'yumi' / 'ui' / 'senses.py').read_text(encoding='utf-8')
assert 'ears.listen' in src, 'senses.py lost the ears.listen call after wake'
assert 'wake_up(t' in src, 'senses.py lost the wake_up call'
# the fallback flag and the user's own action words travel into the wake and back
assert 'hints=self.wake_hints(b)' in src, 'senses.py lost the actions.json hints'
assert "res.get('fallback')" in src, 'senses.py lost the fallback flag'
assert 'wake_up(t, a, f)' in src, 'senses.py lost the fallback flag on the way to the brain'
# «юми» услышана: мотылек становится девочкой сразу, ещё до фразы
assert 'wake_form' in src, 'senses.py lost the wake_form call on the wake'
# stop branch present and calls brain
m = re.search(r"if res\.get\('stop'\):\n(.*?)\n(\s*)continue", src, re.S)
assert m, 'the wake loop lost its stop branch'
assert 'b.stop_all' in m.group(1), 'stop must reach the brain'

# ---- alt (чтение стоп-ухи) доходит с пробуждением до мозгов ----
src_brain = (_root / 'yumi' / 'brain.py').read_text(encoding='utf-8')
assert 'def wake_up(self, text, alt=None, fallback=False)' in src_brain, \
    'brain.wake_up lost the alt or the fallback flag'
assert 'alt=alt' in src_brain and 'fallback=fallback' in src_brain, \
    'wake_up does not pass alt/fallback into heard_text'
src_ears = (_root / 'yumi' / 'ears.py').read_text(encoding='utf-8')
assert "'alt': str(data.get('alt') or '')" in src_ears, 'ears.wake drops the alt'
assert 'q[\'hints\'] = json.dumps' in src_ears, 'ears.wake drops the actions.json hints'
src_stt = (_root / 'yumi' / 'stt_daemon.py').read_text(encoding='utf-8')
assert "'alt': res.get('alt')" in src_stt, 'the /wake answer drops the alt'
assert "'fallback': bool(res.get('fallback'))" in src_stt, 'the /wake answer drops the fallback flag'

# ---- канал стопа и mute в wake(): разделы 8 и 9 ТЗ ----
wake_src = src_stt.split('def wake(self, lang', 1)[1].split('def stop(self', 1)[0]
assert 'if muted():' in wake_src, 'the wake must honor the mute (her own voice on the air)'
assert 'stop_shout(words, spellings)' in wake_src, '«стоп» must cut through the mute'
assert 'read_tape' in wake_src, 'the phrase after the name must go to STT'
assert 'bytearray(ring[-lead:])' in wake_src, 'the phrase head must come off the ring'
assert 'elif ear_oww is None' in wake_src, 'vosk fallback: wake_ear vosk must not be deaf'
assert 'wake_from_vosk' in wake_src, 'the vosk wake lost its shared body'
assert 'fallback_route(tail' in wake_src, \
    'the trained ear must let a routed command through when it misses the name'
assert 'fallback_route(tail, hints)' in wake_src, 'the wake lost the actions.json hints'
# the fallback is not notes-only: commands and phrases meant for her pass,
# background talk does not
assert stt.fallback_route('последняя заметка') == 'command'
assert stt.fallback_route('запиши заметку купить молоко') == 'command'
assert stt.fallback_route('включи музыку') == 'command'
assert stt.fallback_route('как дела?') == 'chat'
assert stt.fallback_route('терминал') == 'ignore'
assert stt.fallback_route('терминал', hints=['терминал']) == 'command'
assert stt.fallback_route('привет как дела') == 'chat'
assert stt.fallback_route('просто болтовня ни о чем') == 'ignore'
assert stt.note_command('последняя заметка')
assert stt.note_command('последний заметка')
assert stt.note_command('запиши заметку купить молоко')
assert stt.note_command('find my last note')
assert not stt.note_command('включи музыку')
assert not stt.note_command('как дела')
assert "if not woken or stop_shout(words, spellings)" in wake_src, \
    'already listening, «останови музыку» is a command, not a stop'
# ---- ухо фразы: Nemotron первым, whisper фоллбэком, Vosk последним ----
assert 'stt_fast' not in wake_src, \
    'the ambient vosk final must not stand in for STT (it reads the room, not the user)'
assert 'read_tape(' in wake_src, 'the phrase after the name must be read from the tape'
assert wake_src.index('read_tape(') < \
    wake_src.index('if not text and heard_after and (fallback_wake or ear_oww is None)'), \
    'the tape is read first: the small ear keeps only a wake that came from it'
assert 'fallback_route(text, hints)' in wake_src, \
    'a weak (vosk) reading of the room must not become a command'
tape_src = src_stt.split('def read_tape(self', 1)[1].split('def transcribe_said', 1)[0]
assert tape_src.index('transcribe_nemo_model') < tape_src.index("engine != 'whisper'") \
    < tape_src.index('vosk_read'), \
    'the read order must be nemotron -> whisper (fallback) -> vosk (last)'


# ---- stop_at / stop_shout unchanged ----
assert stt.stop_at('стоп')
assert stt.stop_at('юми стоп')
assert not stt.stop_at('включи музыку')
assert stt.stop_shout('стоп')
assert not stt.stop_shout('скажи стоп')

print('test_oww: ok')
sys.exit(0)
