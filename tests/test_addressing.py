#!/usr/bin/env python3
"""What a fallback phrase after the name is: a command, a word for her, or noise.

Run: python3 tests/test_addressing.py
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from yumi import addressing  # noqa: E402

# commands run, the mouth does not need the mind
for said in ('включи музыку', 'выключи свет', 'поставь таймер на пять минут',
             'открой терминал', 'запиши заметку купить молоко', 'последняя заметка',
             'расскажи анекдот', 'погода на завтра', 'время',
             'сколько времени', 'громче', 'стоп',
             'play the music', 'open terminal', 'what time is it'):
    assert addressing.route(said) == 'command', said

# questions, greetings and the second person are meant for her
for said in ('как дела', 'что нового', 'где ты', 'почему небо синее',
             'который час', 'ты меня слышишь', 'привет', 'спасибо', 'доброе утро',
             'мне скучно', 'я хочу пиццу', 'я устал', 'i want pizza',
             'how are you', 'what is your name', 'hello', 'thanks'):
    assert addressing.route(said) == 'chat', said

# background talk with no address at all is nobody's wish
for said in ('просто болтовня ни о чем', 'сегодня хорошая погода в парке',
             'стопка книг на столе',
             'он сказал что придет позже', 'the meeting starts at nine'):
    assert addressing.route(said) == 'ignore', said

# a question mark alone is enough (whisper sometimes keeps it)
assert addressing.route('кофе?') == 'chat'

# the user's own actions are known by their phrases and keywords
hints = ['терминал', 'открой браузер', 'плеер']
assert addressing.route('терминал', hints) == 'command'
assert addressing.route('терминай', hints) == 'command'
assert addressing.route('открой браузер пожалуйста', hints) == 'command'
assert addressing.route('плеер', hints) == 'command'
assert addressing.route('просто болтовня', hints) == 'ignore'

# one shape for the ears and the mind: actions.json's own phrases/keywords
assert addressing.hints_from([
    {'phrases': ['терминал', ' '], 'keywords': ['консоль']},
    'junk',
]) == ['терминал', 'консоль']

# the ears use the same answer: notes still pass even without a router word
from yumi import stt_daemon as stt  # noqa: E402
assert stt.fallback_route('последняя заметка') == 'command'
assert stt.fallback_route('включи музыку') == 'command'
assert stt.fallback_route('как дела') == 'chat'
assert stt.fallback_route('просто болтовня ни о чем') == 'ignore'
assert stt.fallback_route('терминал', hints=['терминал']) == 'command'

print('tests/test_addressing.py: ok')
