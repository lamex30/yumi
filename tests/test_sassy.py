#!/usr/bin/env python3
"""«Дерзкая 18+»: off by default, on it changes her mind and her lines.
Run: python3 tests/test_sassy.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yumi import chat, config, phrases  # noqa: E402

# ---- the switch: off by default, a row in the settings and a label in both languages
assert config.DEFAULT_SETTINGS.get('sassy') is False, 'the sassy mode must be off by default'
assert 'sassy' in config.UI['en'] and 'sassy' in config.UI['ru']
assert '18+' in config.UI['ru']['sassy'], config.UI['ru']['sassy']

# ---- the mind: the mood prompt becomes a brat, the default stays the sleepy moth
plain_ru = chat.system_prompt('ru')
plain_en = chat.system_prompt('en')
sassy_ru = chat.system_prompt('ru', sassy=True)
sassy_en = chat.system_prompt('en', sassy=True)
assert '18+' not in plain_ru and '18+' not in plain_en, 'the default prompt must stay polite'
assert 'Дерзкая 18+' in sassy_ru and 'по-быдловски' in sassy_ru, sassy_ru
assert 'Sassy 18+' in sassy_en and 'Profanity is fine' in sassy_en, sassy_en
assert 'Без эмодзи' in sassy_ru and 'No emoji' in sassy_en, 'the no-emoji rule must survive'

# ---- her lines: the sassy pool takes over, missing events fall back to the normal ones
sassy = phrases.Speech({'speech_lang': 'ru', 'sassy': True})
normal = phrases.Speech({'speech_lang': 'ru', 'sassy': False})

sassy_hello = {text for text, _f in phrases.SASSY['ru']['hello']}
seen = set()
for _ in range(12):
    text, _face = sassy.line('hello')
    assert '{' not in text, text
    seen.add(text)
assert seen <= sassy_hello, seen - sassy_hello
assert not (seen & {text for text, _f in phrases.LINES['ru']['hello']}), 'the polite hello leaked in'

seen = set()
for _ in range(12):
    text, _face = normal.line('hello')
    seen.add(text)
assert not (seen & sassy_hello), 'the sassy hello leaked into the polite mode'

# an event the sassy pool does not rename keeps her usual line
whole = sassy.line('weather', report='Анапа: +14°C')
assert whole and 'Анапа: +14°C' in whole[0], whole

# ---- the toggle line exists in both languages and both moods
for lang in ('ru', 'en'):
    assert phrases.LINES[lang].get('sassy_on'), lang
    assert phrases.LINES[lang].get('sassy_off'), lang
    assert phrases.SASSY[lang].get('sassy_on'), lang

print('ok')
