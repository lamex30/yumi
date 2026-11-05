#!/usr/bin/env python3
"""Russian number forms in her lines. Run: python3 tests/test_phrases.py"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('phrases', Path(__file__).resolve().parent.parent / 'yumi' / 'phrases.py')
phrases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phrases)

want = {1: 'файл', 2: 'файла', 4: 'файла', 5: 'файлов', 10: 'файлов', 11: 'файлов', 12: 'файлов', 14: 'файлов',
        20: 'файлов', 21: 'файл', 22: 'файла', 25: 'файлов', 100: 'файлов', 101: 'файл', 111: 'файлов', 112: 'файлов',
        121: 'файл', 122: 'файла'}
for n, word in want.items():
    assert phrases.plural_ru(n, 'файл', 'файла', 'файлов') == word, n

speech = phrases.Speech({'speech_lang': 'ru'})
for _ in range(9):
    text, _face = speech.line('drop_many', n=3)
    assert '{' not in text, text
    assert '3 файла' in text or 'куча' in text, text
print('ok')
