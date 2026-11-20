"""The browser panel's map of Yumi: every setting, where it belongs, and how to show it.

This module is deliberately free of GTK.  It is shared by the HTTP API (validation,
masking, documentation) and by the front-end, which asks ``GET /api/schema`` and draws
the forms itself.  Adding a setting to ``config.DEFAULT_SETTINGS`` is enough for it to
appear here: the fallback below fills a sensible control from the default's type.
"""
from __future__ import annotations

from pathlib import Path

from .. import config, engines

# settings that must never travel to a browser in clear text
SECRET_KEYS = {
    'polza_api_key', 'music_jellyfin_password', 'music_jellyfin_key',
}
MASK = '••••••••'

# the whole panel's navigation; a page may show more than one section
PAGES = [
    {'id': 'dashboard', 'icon': 'home', 'title': {'en': 'Overview', 'ru': 'Обзор'},
     'hint': {'en': 'What Yumi is doing right now', 'ru': 'Что Юми делает сейчас'}},
    {'id': 'chat', 'icon': 'chat', 'title': {'en': 'Talk', 'ru': 'Разговор'},
     'hint': {'en': 'Her mind, her diary and her voice', 'ru': 'Её мозги, дневник и голос'}},
    {'id': 'notes', 'icon': 'pencil', 'title': {'en': 'Notes', 'ru': 'Заметки'},
     'hint': {'en': 'Write by voice or type here, and find them again',
              'ru': 'Записать голосом или вручную и потом найти'}},
    {'id': 'files', 'icon': 'box', 'title': {'en': 'Files', 'ru': 'Файлы'},
     'hint': {'en': 'Recent files, the shelf, convert and compress',
              'ru': 'Недавние, полка, конвертация и сжатие'}},
    {'id': 'music', 'icon': 'note', 'title': {'en': 'Music', 'ru': 'Музыка'},
     'hint': {'en': 'Her library and the room player', 'ru': 'Её библиотека и плеер в комнате'}},
    {'id': 'voice', 'icon': 'mic', 'title': {'en': 'Ears & voice', 'ru': 'Ушки и голос'},
     'hint': {'en': 'Wake word, microphones, TTS', 'ru': 'Имя, микрофоны, настоящий голос'}},
    {'id': 'buttons', 'icon': 'grid', 'title': {'en': 'Buttons', 'ru': 'Кнопки'},
     'hint': {'en': 'Her ring and your own commands', 'ru': 'Кольцо и свои команды'}},
    {'id': 'appearance', 'icon': 'palette', 'title': {'en': 'Look', 'ru': 'Внешний вид'},
     'hint': {'en': 'Colors, outfit, scale and screens', 'ru': 'Цвета, наряд, масштаб и экраны'}},
    {'id': 'settings', 'icon': 'gear', 'title': {'en': 'Settings', 'ru': 'Настройки'},
     'hint': {'en': 'Every switch and value, applied live', 'ru': 'Все переключатели и значения, вживую'}},
    {'id': 'memory', 'icon': 'book', 'title': {'en': 'Memory', 'ru': 'Память'},
     'hint': {'en': 'The diary of talks', 'ru': 'Дневник разговоров'}},
    {'id': 'configs', 'icon': 'file', 'title': {'en': 'Config files', 'ru': 'Конфиги'},
     'hint': {'en': 'The JSON files behind everything', 'ru': 'JSON-файлы, на которых всё держится'}},
    {'id': 'security', 'icon': 'lock', 'title': {'en': 'Security', 'ru': 'Безопасность'},
     'hint': {'en': 'Access, the link, sessions and the audit',
              'ru': 'Доступ, ссылка, сессии и журнал'}},
    {'id': 'logs', 'icon': 'terminal', 'title': {'en': 'Logs', 'ru': 'Журналы'},
     'hint': {'en': 'Her log, the ears\' diary, the panel audit',
              'ru': 'Её лог, дневник ушек, журнал панели'}},
]

# which page owns which section
SECTION_PAGE = {
    'behavior': 'settings', 'sound': 'settings', 'mind': 'settings', 'ears': 'voice',
    'music': 'music', 'look': 'appearance', 'language': 'settings', 'web': 'security',
    'extra': 'settings', 'exports': 'configs',
}

SECTIONS = [
    {'id': 'behavior', 'title': {'en': 'Her behaviour', 'ru': 'Её поведение'},
     'hint': {'en': 'How she lives at the edge of the screen',
              'ru': 'Как она живёт у края экрана'}},
    {'id': 'sound', 'title': {'en': 'Voice and sound', 'ru': 'Голос и звук'},
     'hint': {'en': 'Her 8-bit blips or a real voice',
              'ru': 'Пищалка или настоящий голос'}},
    {'id': 'look', 'title': {'en': 'Look', 'ru': 'Внешний вид'},
     'hint': {'en': 'Colors, outfit, size', 'ru': 'Цвета, наряд, размер'}},
    {'id': 'language', 'title': {'en': 'Language', 'ru': 'Язык'},
     'hint': {'en': 'Interface and spoken lines', 'ru': 'Интерфейс и реплики'}},
    {'id': 'mind', 'title': {'en': 'Her mind', 'ru': 'Её мозги'},
     'hint': {'en': 'The local model, memory and commands',
              'ru': 'Локальная модель, память и команды'}},
    {'id': 'ears', 'title': {'en': 'Her ears', 'ru': 'Её ушки'},
     'hint': {'en': 'What she hears and how she wakes',
              'ru': 'Что она слышит и как просыпается'}},
    {'id': 'music', 'title': {'en': 'Music', 'ru': 'Музыка'},
     'hint': {'en': 'Jellyfin, folders, servers', 'ru': 'Jellyfin, папки, серверы'}},
    {'id': 'web', 'title': {'en': 'Browser panel', 'ru': 'Веб-панель'},
     'hint': {'en': 'The address, the link and the session',
              'ru': 'Адрес, ссылка и сессия'}},
    {'id': 'extra', 'title': {'en': 'Small things', 'ru': 'Мелочи'},
     'hint': {'en': 'Weather, breaks and the rest', 'ru': 'Погода, перерывы и остальное'}},
]

# key -> section (anything not named lands in "extra")
KEY_SECTION = {
    'pinned': 'behavior', 'reactions': 'behavior', 'chatter': 'behavior',
    'hourly_chimes': 'behavior', 'idle_nap': 'behavior', 'breaks': 'behavior',
    'gaze': 'behavior', 'voice_vol': 'sound', 'voice_kind': 'sound',
    'tts_ru_speaker': 'sound', 'tts_en_speaker': 'sound',
    'tts_ru_model': 'sound', 'tts_pitch': 'sound',
    'timer_sound': 'sound', 'recolor': 'look', 'hue': 'look', 'scale': 'look',
    'outfit': 'look', 'ui_lang': 'language', 'speech_lang': 'language',
    'chat_provider': 'mind', 'chat_model': 'mind', 'chat_think': 'mind',
    'sassy': 'mind', 'chat_history_n': 'mind', 'chat_memory': 'mind',
    'notes_voice': 'mind',
    'embed_model': 'mind', 'nlu': 'mind', 'intents': 'mind', 'ollama_url': 'mind',
    'polza_url': 'mind', 'polza_api_key': 'mind',
    'wake_ears': 'ears', 'wake_word': 'ears', 'wake_sensitivity': 'ears',
    'wake_confirm': 'ears', 'wake_model_size': 'ears', 'wake_ear': 'ears',
    'wake_judge_log': 'ears', 'name_duck': 'ears', 'stt_size': 'ears',
    'stt_engine': 'ears', 'stt_mic': 'ears', 'stt_echo': 'ears',
    'stt_selfmute': 'ears', 'phrase_tail_ms': 'ears', 'wake_after': 'ears',
    'wake_read_s': 'ears',
    'tap_ears': 'ears', 'music_skill': 'music', 'music_source': 'music',
    'music_jellyfin': 'music', 'music_jellyfin_user': 'music',
    'music_jellyfin_password': 'music', 'music_jellyfin_key': 'music',
    'music_dirs': 'music', 'music_server': 'music', 'music_stream': 'music',
    'weather_city': 'extra', 'web_panel': 'web', 'web_panel_host': 'web',
    'web_panel_port': 'web', 'web_panel_open': 'web',
    'web_panel_session_hours': 'web',
}

# the short lists Brain cycles through in the in-world menu
CHOICES = {
    'ui_lang': ['en', 'ru'],
    'speech_lang': ['en', 'ru'],
    'breaks': [0, 20, 30, 45, 60],
    'voice_vol': [0, 25, 50, 75, 100],
    'voice_kind': ['blips', 'tts'],
    'tts_ru_speaker': engines.RU_SPEAKERS,
    'tts_en_speaker': engines.EN_SPEAKERS,
    'tts_ru_model': engines.RU_MODELS,
    'stt_engine': ['whisper', 'nemotron'],
    'stt_size': ['small', 'big'],
    'stt_echo': [True, False],
    'stt_selfmute': [True, False],
    'wake_sensitivity': ['normal', 'strict'],
    'wake_confirm': [True, False],
    'wake_model_size': ['small', 'big'],
    'wake_ear': ['auto', 'oww', 'vosk'],
    'wake_judge_log': [True, False],
    'chat_provider': ['ollama', 'polza.ai'],
    'chat_history_n': [2, 4, 6, 10, 16, 24],
    'music_source': ['auto', 'jellyfin', 'local'],
    'outfit': ['auto', 'off'],
    'web_panel_host': ['127.0.0.1', '0.0.0.0'],
}
# labels for choices (key:value); the front-end falls back to the raw value
CHOICE_LABELS = {
    'ui_lang': {'en': 'English', 'ru': 'Русский'},
    'speech_lang': {'en': 'English', 'ru': 'Русский'},
    'voice_kind': {'blips': {'en': '8-bit blips', 'ru': 'Пищалка'},
                   'tts': {'en': 'Real voice', 'ru': 'Настоящий'}},
    'tts_ru_speaker': {'kseniya': 'Kseniya', 'baya': 'Baya', 'xenia': 'Xenia',
                       'aidar': 'Aidar', 'eugene': 'Eugene'},
    'tts_ru_model': {'v5_6_ru': {'en': 'v5.6 (questions)', 'ru': 'v5.6 (вопросы)'},
                     'v4_ru': {'en': 'v4 (older)', 'ru': 'v4 (старая)'}},
    'stt_engine': {'whisper': 'Whisper', 'nemotron': 'Nemotron 0.6b'},
    'stt_size': {'small': {'en': 'Lite', 'ru': 'Лёгкая'},
                 'big': {'en': 'Sharp', 'ru': 'Точная'}},
    'wake_sensitivity': {'normal': {'en': 'Normal', 'ru': 'Обычная'},
                         'strict': {'en': 'Strict', 'ru': 'Строгая'}},
    'wake_model_size': {'small': {'en': 'Lite', 'ru': 'Лёгкая'},
                        'big': {'en': 'Sharp', 'ru': 'Точная'}},
    'wake_ear': {'auto': {'en': 'auto', 'ru': 'авто'},
                 'oww': 'openWakeWord', 'vosk': 'vosk'},
    'music_source': {'auto': {'en': 'auto', 'ru': 'авто'},
                     'jellyfin': 'Jellyfin',
                     'local': {'en': 'my folders', 'ru': 'мои папки'}},
    'outfit': {'auto': {'en': 'auto', 'ru': 'авто'},
               'off': {'en': 'none', 'ru': 'нет'}},
    'web_panel_host': {'127.0.0.1': {'en': 'this machine only', 'ru': 'только этот компьютер'},
                       '0.0.0.0': {'en': 'the local network', 'ru': 'локальная сеть'}},
}

# what each control is; everything else is inferred from the default
TYPES = {
    'scale': 'range', 'voice_vol': 'range', 'hue': 'range', 'breaks': 'select',
    'chat_history_n': 'select', 'web_panel_port': 'number',
    'web_panel_session_hours': 'number', 'phrase_tail_ms': 'number',
    'wake_after': 'number', 'wake_read_s': 'number',
    'timer_sound': 'path', 'tts_pitch': 'range',
    'tts_ru_speaker': 'select', 'tts_en_speaker': 'select',
    'tts_ru_model': 'select',
    'music_jellyfin_password': 'password', 'polza_api_key': 'password',
    'music_jellyfin_key': 'password', 'music_dirs': 'text',
    'web_panel': 'switch', 'web_panel_open': 'switch',
}

RANGES = {
    'scale': (1, 4, 1), 'voice_vol': (0, 100, 5), 'hue': (0, 1, 0.01),
    'web_panel_port': (1024, 65535, 1), 'web_panel_session_hours': (1, 8760, 1),
    'phrase_tail_ms': (200, 5000, 50), 'wake_after': (0, 10, 0.1),
    'wake_read_s': (2, 10, 0.5),
    'tts_pitch': (0.7, 1.5, 0.01),
}

TITLES = {
    'pinned': {'en': 'Stay out', 'ru': 'Не прятаться'},
    'reactions': {'en': 'System reactions', 'ru': 'Реакции на систему'},
    'chatter': {'en': 'Chatter', 'ru': 'Болтовня'},
    'hourly_chimes': {'en': 'Time every hour', 'ru': 'Время каждый час'},
    'idle_nap': {'en': 'Nap when idle', 'ru': 'Дремать без тебя'},
    'breaks': {'en': 'Health breaks', 'ru': 'Перерывы для здоровья'},
    'gaze': {'en': 'Eyes follow the pointer', 'ru': 'Глаза следят за курсором'},
    'voice_vol': {'en': 'Voice volume', 'ru': 'Громкость голоска'},
    'voice_kind': {'en': 'Voice type', 'ru': 'Тип голоска'},
    'tts_ru_speaker': {'en': 'Russian voice', 'ru': 'Русский голос'},
    'tts_en_speaker': {'en': 'English voice', 'ru': 'Английский голос'},
    'tts_ru_model': {'en': 'Russian voice model', 'ru': 'Модель русского голоса'},
    'tts_pitch': {'en': 'Voice pitch', 'ru': 'Высота голоса'},
    'timer_sound': {'en': 'Timer sound', 'ru': 'Звук таймера'},
    'recolor': {'en': 'Wallpaper colors', 'ru': 'Цвета от обоев'},
    'hue': {'en': 'Manual hue', 'ru': 'Оттенок вручную'},
    'scale': {'en': 'Pixel scale', 'ru': 'Масштаб пикселей'},
    'outfit': {'en': 'Outfit', 'ru': 'Наряд'},
    'ui_lang': {'en': 'Interface language', 'ru': 'Язык интерфейса'},
    'speech_lang': {'en': 'Spoken language', 'ru': 'Язык реплик'},
    'chat_provider': {'en': 'Mind provider', 'ru': 'Провайдер мозгов'},
    'chat_model': {'en': 'Answer model', 'ru': 'Модель ответов'},
    'chat_think': {'en': 'Think before answering', 'ru': 'Думать перед ответом'},
    'sassy': {'en': 'Sassy 18+', 'ru': 'Дерзкая 18+'},
    'chat_history_n': {'en': 'Messages in the mind', 'ru': 'Сообщений в памяти'},
    'chat_memory': {'en': 'Remember older talks', 'ru': 'Помнить старые разговоры'},
    'embed_model': {'en': 'Embedding model', 'ru': 'Модель векторов'},
    'notes_voice': {'en': 'Notes by voice', 'ru': 'Заметки голосом'},
    'nlu': {'en': 'Voice commands', 'ru': 'Голосовые команды'},
    'intents': {'en': 'Intents mind', 'ru': 'Распознавание фраз'},
    'ollama_url': {'en': 'Ollama address', 'ru': 'Адрес Ollama'},
    'polza_url': {'en': 'Polza.ai address', 'ru': 'Адрес Polza.ai'},
    'polza_api_key': {'en': 'Polza.ai API key', 'ru': 'Ключ Polza.ai'},
    'wake_ears': {'en': 'Wake on the name', 'ru': 'Будить по имени'},
    'wake_word': {'en': 'Wake word', 'ru': 'Имя для пробуждения'},
    'wake_sensitivity': {'en': 'Name matching', 'ru': 'Строгость имени'},
    'wake_confirm': {'en': 'Whisper double-check', 'ru': 'Проверка whisper-ом'},
    'wake_model_size': {'en': 'Name ear size (vosk)', 'ru': 'Размер уха имени (vosk)'},
    'wake_ear': {'en': 'The ear that hears the name', 'ru': 'Ухо, которое слышит имя'},
    'wake_judge_log': {'en': 'Judge log', 'ru': 'Лог судьи'},
    'name_duck': {'en': 'Duck sound on the name', 'ru': 'Приглушать звук при имени'},
    'stt_size': {'en': 'Phrase ear size', 'ru': 'Размер уха для фраз'},
    'stt_engine': {'en': 'Phrase judge (STT)', 'ru': 'Судья фраз (STT)'},
    'stt_mic': {'en': 'Microphone', 'ru': 'Микрофон'},
    'stt_echo': {'en': 'Echo canceller', 'ru': 'Подавление эха'},
    'stt_selfmute': {'en': 'Close ears while speaking', 'ru': 'Закрывать ушки, пока говорит'},
    'phrase_tail_ms': {'en': 'Quiet before the phrase ends (ms)', 'ru': 'Тишина до конца фразы (мс)'},
    'wake_after': {'en': 'Quiet after the name (s)', 'ru': 'Тишина после имени (с)'},
    'wake_read_s': {'en': 'Phrase window after the name (s)',
                    'ru': 'Окно фразы после имени (с)'},
    'tap_ears': {'en': 'The ears\' diary', 'ru': 'Дневник ушек'},
    'music_skill': {'en': 'Music by voice', 'ru': 'Музыка голосом'},
    'music_source': {'en': 'Where songs come from', 'ru': 'Откуда брать песни'},
    'music_jellyfin': {'en': 'Jellyfin address', 'ru': 'Адрес Jellyfin'},
    'music_jellyfin_user': {'en': 'Jellyfin login', 'ru': 'Логин Jellyfin'},
    'music_jellyfin_password': {'en': 'Jellyfin password', 'ru': 'Пароль Jellyfin'},
    'music_jellyfin_key': {'en': 'Jellyfin API key', 'ru': 'API-ключ Jellyfin'},
    'music_dirs': {'en': 'Extra music folders', 'ru': 'Доп. папки с музыкой'},
    'music_server': {'en': 'Remote mpd', 'ru': 'Удалённый mpd'},
    'music_stream': {'en': 'Remote stream', 'ru': 'Поток с сервера'},
    'weather_city': {'en': 'Weather city', 'ru': 'Город для погоды'},
    'web_panel': {'en': 'Browser panel', 'ru': 'Панель в браузере'},
    'web_panel_host': {'en': 'Who can open it', 'ru': 'Кто может открыть'},
    'web_panel_port': {'en': 'Port', 'ru': 'Порт'},
    'web_panel_open': {'en': 'Open the browser on enable', 'ru': 'Открывать браузер при включении'},
    'web_panel_session_hours': {'en': 'Session lifetime (hours)', 'ru': 'Срок сессии (часов)'},
}

HINTS = {
    'pinned': {'en': 'She does not hide at the bottom edge.',
               'ru': 'Она не прячется у нижнего края.'},
    'reactions': {'en': 'Charger, Wi-Fi, downloads, screenshots, music.',
                  'ru': 'Зарядка, Wi-Fi, загрузки, скриншоты, музыка.'},
    'chatter': {'en': 'Quiet small talk while she is out, at most every 10 minutes.',
                'ru': 'Тихая болтовня, пока она снаружи, не чаще раза в 10 минут.'},
    'hourly_chimes': {'en': 'She reads the clock with a small joke every hour.',
                      'ru': 'Каждый час она говорит время с шуткой.'},
    'idle_nap': {'en': 'Dozes off when you have been away for a while.',
                 'ru': 'Засыпает, когда вас долго нет.'},
    'breaks': {'en': 'Water, eyes and a stretch every N minutes (0 = off).',
               'ru': 'Вода, глаза и разминка каждые N минут (0 = выкл).'},
    'gaze': {'en': 'Her eyes follow the pointer around the screen.',
             'ru': 'Её глаза следят за курсором по экрану.'},
    'timer_sound': {'en': 'Empty = her 8-bit chime. Any WAV or OGG file works.',
                    'ru': 'Пусто = её 8-битный колокольчик. Подойдёт WAV или OGG.'},
    'tts_ru_speaker': {'en': 'The Silero speaker for Russian lines. A voice change restarts the voice daemon (a few seconds).',
                       'ru': 'Голос Silero для русских реплик. Смена голоса перезапускает демон озвучки (несколько секунд).'},
    'tts_en_speaker': {'en': 'The Silero speaker for English lines (en_0 … en_117).',
                       'ru': 'Голос Silero для английских реплик (en_0 … en_117).'},
    'tts_ru_model': {'en': 'v5.6 hears questions and exclamations; v4 is the older voice. Both live in the cache.',
                     'ru': 'v5.6 слышит вопросы и восклицания; v4 — старая модель. Обе лежат в кэше.'},
    'tts_pitch': {'en': '1.0 = as recorded, 1.2 = a proper squeak. Applies to both voices.',
                  'ru': '1.0 — как записано, 1.2 — уже пищалка. Работает для обоих голосов.'},
    'hue': {'en': 'Used when wallpaper colors are off. 0…1 around the color wheel.',
            'ru': 'Работает, когда цвета от обоев выключены. 0…1 по кругу цветов.'},
    'scale': {'en': 'Whole interface pixels. A restart is needed for the window itself.',
              'ru': 'Целые пиксели интерфейса. Для самого окна нужен перезапуск.'},
    'chat_model': {'en': 'Installed Ollama models are listed after a refresh.',
                   'ru': 'Установленные модели Ollama появятся после обновления.'},
    'sassy': {'en': 'A bratty, swearing 18+ mood. Off by default; leaving it wipes its diary lines.',
              'ru': 'Дерзкая, матерящаяся натура 18+. По умолчанию выкл; при выключении её строки стираются.'},
    'chat_memory': {'en': 'The sqlite diary in ~/.local/state/yumi/yumi.db.',
                    'ru': 'Дневник sqlite в ~/.local/state/yumi/yumi.db.'},
    'notes_voice': {'en': 'Say «запиши заметку …» to write one and «найди заметку про …» to search them.',
                    'ru': 'Скажи «запиши заметку …», чтобы записать, и «найди заметку про …», чтобы искать.'},
    'wake_word': {'en': 'Say it, then the question. «Юми» by default.',
                  'ru': 'Скажи его, потом вопрос. По умолчанию «Юми».'},
    'stt_engine': {'en': 'Who reads the phrase after the name. Nemotron reads first (fast, needs its model); whisper is the fallback when it is away or silent. Only when both are silent does the lite ear read the tape.',
                   'ru': 'Кто читает фразу после имени. Первым читает Nemotron (быстрый, требует свою модель); whisper — фоллбэк, если её нет или она молчит. Лёгкое ухо читает ленту только когда молчат оба.'},
    'wake_read_s': {'en': 'How much of the phrase after the name the ear reads. A command is short: a longer window adds latency, not words.',
                    'ru': 'Сколько фразы после имени читает ухо. Команда короткая: длинное окно добавляет задержку, а не слова.'},
    'stt_mic': {'en': 'The list comes from the sound system; “auto” leaves the choice to the desktop.',
                'ru': 'Список берётся из звуковой системы; «авто» оставляет выбор рабочему столу.'},
    'music_source': {'en': 'Auto: Jellyfin at home, her folders away.',
                     'ru': 'Авто: Jellyfin дома, свои папки вне дома.'},
    'web_panel': {'en': 'Opens a local HTTP server. Off by default; the token is required.',
                  'ru': 'Открывает локальный HTTP-сервер. По умолчанию выкл; нужен токен.'},
    'web_panel_host': {'en': '127.0.0.1 keeps it on this machine. 0.0.0.0 exposes it to the LAN — only with a strong token and a firewall.',
                       'ru': '127.0.0.1 оставляет панель на этом компьютере. 0.0.0.0 открывает её в локальную сеть — только с надёжным токеном и файрволом.'},
    'web_panel_session_hours': {'en': 'How long the browser stays signed in before the token is asked again.',
                                'ru': 'Сколько браузер остаётся в системе, прежде чем снова спросить токен.'},
}

CONFIG_FILES = {
    'settings': {'path': lambda: config.CONFIG_DIR / 'settings.json',
                 'title': {'en': 'Settings', 'ru': 'Настройки'}, 'kind': 'settings'},
    'actions': {'path': lambda: config.ACTIONS_FILE,
                'title': {'en': 'Custom buttons', 'ru': 'Свои кнопки'}, 'kind': 'actions'},
    'tts': {'path': lambda: config.CONFIG_DIR / 'tts.json',
            'title': {'en': 'Voice profile', 'ru': 'Профиль голоса'}, 'kind': 'json'},
    'stress': {'path': lambda: config.CONFIG_DIR / 'stress.json',
               'title': {'en': 'Stress marks', 'ru': 'Ударения'}, 'kind': 'json'},
    'palette': {'path': lambda: config.CONFIG_DIR / 'palette.json',
                'title': {'en': 'Palette override', 'ru': 'Своя палитра'}, 'kind': 'json'},
    'state': {'path': lambda: config.STATE_DIR / 'state.json',
              'title': {'en': 'Saved state', 'ru': 'Сохранённое состояние'}, 'kind': 'state'},
    'webpanel': {'path': lambda: Path(config.CONFIG_DIR) / 'webpanel.json',
                 'title': {'en': 'Panel access', 'ru': 'Доступ к панели'}, 'kind': 'secret'},
}


def _choice_value(key, value):
    return CHOICE_LABELS.get(key, {}).get(str(value), value)


def _outfit_choices():
    try:
        from ..sprites import Meta
        return ['auto', 'off'] + Meta.outfits(config.SPRITES)
    except Exception:
        return ['auto', 'off']


def _entry(key, default):
    kind = TYPES.get(key)
    if kind is None:
        if isinstance(default, bool):
            kind = 'switch'
        elif isinstance(default, int):
            kind = 'number'
        elif isinstance(default, float):
            kind = 'range'
        elif isinstance(default, list):
            kind = 'list'
        else:
            kind = 'text'
    if key == 'hue':
        kind = 'range'
    choices = None
    if key == 'outfit':
        choices = _outfit_choices()
    elif key in CHOICES:
        choices = list(CHOICES[key])
    elif key == 'stt_mic':
        choices = [None]  # filled by the live status (microphones)
    if kind == 'select' and not choices:
        kind = 'text'
    elif kind == 'text' and choices:
        kind = 'select'
    rng = RANGES.get(key)
    section = KEY_SECTION.get(key, 'extra')
    return {
        'key': key,
        'section': section,
        'page': SECTION_PAGE.get(section, 'settings'),
        'type': kind,
        'default': default,
        'title': TITLES.get(key) or {'en': key.replace('_', ' ').title(), 'ru': key},
        'hint': HINTS.get(key) or {},
        'choices': choices,
        'choice_labels': {str(v): _choice_value(key, v) for v in (choices or [])},
        'min': rng[0] if rng else None,
        'max': rng[1] if rng else None,
        'step': rng[2] if rng else None,
        'secret': key in SECRET_KEYS,
        'restart': key == 'scale',
        'live': key not in ('scale',),
    }


def schema():
    """the complete form map: sections, pages, config files and setting entries"""
    keys = list(config.DEFAULT_SETTINGS.keys())
    # keys the code reads but the defaults forgot: show them too, never hide a knob
    for extra in ('music_jellyfin_key',):
        if extra not in keys:
            keys.append(extra)
    entries = []
    for key in keys:
        default = config.DEFAULT_SETTINGS.get(key)
        if key == 'music_jellyfin_key':
            default = ''
        entries.append(_entry(key, default))
    return {
        'version': __import__('yumi').__version__,
        'pages': PAGES,
        'sections': SECTIONS,
        'settings': entries,
        'files': {k: {'title': v['title'], 'kind': v['kind'],
                      'path': str(v['path']())} for k, v in CONFIG_FILES.items()},
        'secret_mask': MASK,
    }


def coerce(key, value):
    """validate and normalize one value from a browser; raises ValueError"""
    default = config.DEFAULT_SETTINGS.get(key, '')
    if key in ('hue',):
        if value in (None, ''):
            return None
        number = float(value)
        if not 0 <= number <= 1:
            raise ValueError('hue must be between 0 and 1')
        return round(number, 4)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'on', 'yes')
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        number = int(value)
        rng = RANGES.get(key)
        if rng and not rng[0] <= number <= rng[1]:
            raise ValueError(f'{key} must be between {rng[0]} and {rng[1]}')
        return number
    if isinstance(default, float):
        number = float(value)
        rng = RANGES.get(key)
        if rng and not rng[0] <= number <= rng[1]:
            raise ValueError(f'{key} must be between {rng[0]} and {rng[1]}')
        return number
    if isinstance(default, list):
        if isinstance(value, str):
            value = [v.strip() for v in value.split(',') if v.strip()]
        if not isinstance(value, list):
            raise ValueError(f'{key} must be a list')
        return [str(v) for v in value]
    if default is None and value in (None, ''):
        return None
    if key == 'stt_mic' and value in (None, '', 'auto', 'None'):
        return None
    text = '' if value is None else str(value)
    if len(text) > 4000:
        raise ValueError(f'{key} is too long')
    return text


def redact(settings):
    """a copy safe to send: secrets keep only the fact that they are set"""
    out = dict(settings)
    for key in SECRET_KEYS:
        value = str(out.get(key) or '')
        out[key] = MASK if value else ''
        out.setdefault(f'{key}__set', bool(value))
    return out


def is_masked(value):
    return isinstance(value, str) and value == MASK


def validate_settings_patch(patch):
    """{key: value} -> normalized patch; unknown keys are rejected"""
    if not isinstance(patch, dict):
        raise ValueError('settings patch must be an object')
    clean = {}
    known = set(config.DEFAULT_SETTINGS) | {'music_jellyfin_key'}
    for key, value in patch.items():
        if key not in known:
            raise ValueError(f'unknown setting: {key}')
        if key in SECRET_KEYS and is_masked(value):
            continue
        clean[key] = coerce(key, value)
    return clean
