"""Paths, settings, persistent state and interface strings (no GTK here)."""
import hashlib
import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SPRITES = HERE / 'sprites'
SOUNDS = HERE / 'sounds'
_cfg = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
_state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
_cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
CONFIG_DIR = _cfg / 'yumi'
STATE_DIR = _state / 'yumi'
CACHE_DIR = _cache / 'yumi'

DEFAULT_SETTINGS = {
    'ui_lang': 'en',          # interface: en / ru
    'speech_lang': 'en',      # her lines: en / ru
    'reactions': True,        # reactions to the system (charger, Wi-Fi, downloads...)
    'pinned': False,          # she stays out and does not hide at the bottom edge
    'chatter': True,          # idle small talk (at most every 10 minutes, only while she is out)
    'voice_vol': 50,          # her voice while her words type out, % (0 = silent)
    'voice_kind': 'blips',    # her voice: blips (8-bit) / tts (a real one, tools/tts_setup.sh)
    'scale': 1,               # integer pixel scale of the sprites
    'recolor': True,          # follow the wallpaper palette
    'hue': None,              # manual color when not following: None = her own colors, else 0..1
    'timer_sound': None,      # None = her 8-bit chime (sounds/timer.wav), else a path
    'breaks': 0,              # health reminders every N minutes (0 = off)
    'idle_nap': True,         # she dozes off when you have been away for a while
    'outfit': 'auto',         # auto (by date) / off / a folder name in sprites/outfits
    'ring': [],               # order of her round buttons ([] = the default order)
    'ring_off': [],           # the buttons switched off
    'chat_provider': 'ollama',   # ollama or polza.ai: who answers when she listens
    'chat_model': 'lakomoor/vikhr-llama-3.2-1b-instruct:1b',   # who answers when she listens
    'ollama_url': 'http://127.0.0.1:11434',   # where her local Ollama lives
    'polza_url': 'https://polza.ai/api/v1',   # Polza.ai OpenAI-compatible endpoint
    'polza_api_key': '',   # Polza.ai API key (get it at polza.ai)
    'wake_ears': True,        # she starts listening the moment you say the wake word
    'music_skill': True,      # music control via voice commands
    'wake_word': 'юми',       # the wake word itself (say it, then the question);
                              # the ears learn its hear-says into
                              # ~/.local/share/yumi/stt/hearsay.json, edit freely
    'wake_sensitivity': 'normal',   # normal / strict: how sharply the wake word must match
    'wake_confirm': True,   # let whisper double-check every vosk guess before waking
    'wake_model_size': 'small',   # small / big: the model the wake ear listens with
    'wake_judge_log': True,    # write what the judge hears into /tmp/yumi/judge.log
    'stt_size': 'small',      # her hearing: small (45 MB) / big (a sharper ~1.8 GB model)
    'stt_mic': None,          # which microphone the ears hear with: None = auto (the desktop's pick)
    'ears_range': 'mid',      # how far the ears reach: near / mid / far (the noise gate decides)
    'tap_ears': False,        # a diary of everything the ears hear: /tmp/yumi (sound + words)
    'hourly_chimes': True,    # she speaks the time with a joke every hour
    'weather_city': 'Анапа',  # the city she peeks the weather at ('' = wherever she is);
                              # say it in the phrase («юми погода в ялте») or edit
                              # ~/.config/yumi/settings.json → "weather_city": "Ялта"
}
DEFAULT_STATE = {
    'mode': 'docked',         # docked / air
    'dock_x': None,           # foot x when docked (None = bottom-right corner)
    'air': None,              # [x, y] foot position when floating
    'shelf': [],              # paths kept on her shelf
    'colors': [],             # pipette history, newest first
    'intro_done': False,      # the first-run tour has been shown
    'monitor': None,          # connector name of the screen she lives on (None = first one)
}


def detect_lang():
    """'ru' when the system language is Russian, otherwise 'en'"""
    for var in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        val = os.environ.get(var, '')
        if val:
            return 'ru' if val.lower().replace('-', '_').startswith('ru') else 'en'
    return 'en'


class Store(dict):
    """A dict saved as JSON; unknown keys from older versions are kept, missing ones defaulted."""

    def __init__(self, path, defaults):
        super().__init__(defaults)
        self.path = Path(path)
        self.fresh = not self.path.exists()
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                self.update(data)
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self, indent=1, ensure_ascii=False))
            tmp.replace(self.path)
        except OSError:
            pass


def load_settings():
    st = Store(CONFIG_DIR / 'settings.json', DEFAULT_SETTINGS)
    if st.fresh:                                  # first run: speak the system language
        st['ui_lang'] = st['speech_lang'] = detect_lang()
        st.save()
    return st


RING_SLOTS = 10            # places on her ring in the button editor


def load_actions(path=None):
    """her own round buttons from ~/.config/yumi/actions.json:
    [{"label": "Terminal", "label_ru": "Терминал", "icon": "gear", "command": "foot"}]"""
    path = Path(path) if path else CONFIG_DIR / 'actions.json'
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise ValueError
    except OSError:
        return []
    except ValueError:                    # the editor would overwrite a file with a typo in it
        try:
            path.replace(path.with_name(path.name + '.bad'))
        except OSError:
            pass
        return []
    out = []
    for a in data:
        if isinstance(a, dict) and a.get('command'):
            out.append({'label': str(a.get('label') or a['command'])[:18],
                        'label_ru': str(a.get('label_ru') or a.get('label') or a['command'])[:18],
                        'icon': str(a.get('icon') or 'star'), 'command': str(a['command'])})
    return out


def save_actions(actions, path=None):
    path = Path(path) if path else CONFIG_DIR / 'actions.json'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(actions, indent=1, ensure_ascii=False))
        tmp.replace(path)
    except OSError:
        pass


def keep_icon(src):
    """a copy of the chosen picture in the config, so it outlives the original file"""
    src = Path(src)
    try:
        # shortcut: copies stay behind when a button is deleted (two buttons may share one), they are tiny
        out = CONFIG_DIR / 'icons' / (hashlib.sha1(src.read_bytes()).hexdigest()[:16] + src.suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        return str(out)
    except OSError:
        return str(src)


def outfit_for(today=None):
    """the outfit folder the calendar asks for (None = her usual clothes)"""
    import datetime
    d = today or datetime.date.today()
    md = (d.month, d.day)
    for name, (a, b) in OUTFIT_DATES.items():
        inside = a <= md <= b if a <= b else (md >= a or md <= b)
        if inside:
            return name
    return None


OUTFIT_DATES = {            # name: ((month, day) from, (month, day) to)
    'halloween': ((10, 24), (11, 2)),
    'newyear': ((12, 15), (1, 10)),
    'valentine': ((2, 12), (2, 15)),
    'spring': ((3, 6), (3, 9)),
}


def load_state():
    return Store(STATE_DIR / 'state.json', DEFAULT_STATE)


# ---- interface strings ----
UI = {
    'en': {
        'recent': 'Recent', 'shelf': 'Shelf', 'pipette': 'Pipette', 'clock': 'Time',
        'sleep': 'DND', 'wake': 'Wake up', 'settings': 'Settings', 'home': 'Go home', 'quit': 'Quit',
        'convert': 'Convert', 'compress': 'Compress', 'keep': 'Keep on shelf',
        'unkeep': 'Off the shelf', 'back': 'Back', 'empty_recent': 'Nothing new yet',
        'empty_shelf': 'Drop files on me to keep them here', 'ui_lang': 'Interface',
        'speech_lang': 'Speech', 'reactions': 'System reactions', 'chatter': 'Chatter', 'pinned': 'Stay out',
        'on': 'on', 'off': 'off', 'colors': 'Colors', 'to': 'to', 'working': 'Working',
        'open': 'Open', 'files': '{n} files', 'cancel': 'Cancel',
        'p_small': 'Smaller', 'p_1mb': 'Under 1 MB', 'p_half': 'Half size',
        'p_10': 'Under 10 MB', 'p_25': 'Under 25 MB', 'p_50': 'Under 50 MB',
        'p_720': '720p', 'p_480': '480p', 'p_opus': 'Opus 96k', 'p_mp3': 'MP3 128k',
        'p_lossless': 'Lossless',
        'convert_to': 'Convert to', 'compress_how': 'Compress', 'follow': 'Wallpaper colors',
        'timer': 'Timer', 'pause': 'Pause', 'resume': 'Start', 'reset': 'Reset', 'stop': 'Stop', 'picked': 'Picked',
        'timer_sound': 'Timer sound', 'sound_default': '8-bit', 'sound_reset': 'Default sound',
        'sound_test': 'Listen', 'audio_files': 'Sounds',
        'hint': 'Drag a file onto me to convert, compress or keep it',
        'breaks': 'Health breaks', 'idle_nap': 'Nap when idle', 'outfit': 'Outfit',
        'voice': 'Voice', 'voice_kind': 'Voice type', 'v_blips': '8-bit', 'v_tts': 'Real voice',
        'cat_her': 'Her', 'cat_sound': 'Sound', 'cat_look': 'Look', 'cat_lang': 'Language', 'n_a': 'not available yet', 'o_auto': 'auto', 'o_off': 'none', 'o_halloween': 'Halloween', 'o_newyear': 'New Year',
        'o_valentine': 'Valentine', 'o_spring': 'March 8', 'min': 'min', 'monitor': 'Screen',
        'cat_ring': 'Buttons', 'ring_done': 'Done', 'ring_new': 'New button', 'ring_change': 'Edit button', 'ring_save': 'Save',
        'ring_cmd': 'Command', 'ring_name': 'Button name', 'ring_icon': 'Icon', 'ring_file': 'Your own picture...',
        'ring_add': 'Add', 'images': 'Pictures',
        'restart': 'Restart', 'tour_again': 'Show the tour', 'tour_next': 'Next', 'tour_skip': 'Skip', 'tour_done': 'Got it!',
        'tour_title': 'Hello!',
        'listen': 'Listen', 'cat_chat': 'Her mind', 'chat_model': 'Answer model',
        'joke': 'Joke',
        'chat_refresh': 'Find the model',
        'wake_ears': 'Wake on «Yumi»', 'stt_size': 'Hearing accuracy', 'e_small': 'Lite',
        'e_big': 'Sharp (downloads ~1.8 GB)', 'cat_ears': 'Her ears', 'stt_mic': 'Microphone',
        'wake_sensitivity': 'Wake strictness', 'wake_sensitivity_normal': 'Normal',
        'wake_sensitivity_strict': 'Strict', 'wake_confirm': 'Judge each guess',
        'wake_model_size': 'Wake model', 'wake_judge_log': 'Log the judge to /tmp/yumi',
        'mic_refresh': 'Find the microphone', 'mic_auto': 'auto', 'chat_provider': 'Mind provider',
        'polza_url': 'Polza.ai address', 'polza_api_key': 'Polza.ai API key',
        'tap_ears': 'Ears diary (/tmp/yumi)', 'ears_range': 'Ear range',
        'r_near': 'Close', 'r_mid': 'Normal', 'r_far': 'Across the room',
        'hourly_chimes': 'She says the time every hour',
        'tour1': "Hi! I'm Yumi. I live at the bottom edge of your screen. Let me show you around.",
        'tour2': 'Click me and round buttons appear: files, shelf, pipette, timer, Do Not Disturb and settings.',
        'tour3': 'Scroll the wheel over me for a timer. Click its digits to type a time.',
        'tour4': 'Drop files on me to convert or compress them. Hold me by the scruff to move me anywhere.',
        'tour5': 'Middle-click pins me so I stop hiding. The rest is in the gear: language, colors, outfits.',
        'B': 'B', 'KB': 'KB', 'MB': 'MB', 'GB': 'GB',
    },
    'ru': {
        'recent': 'Недавние', 'shelf': 'Полка', 'pipette': 'Пипетка', 'clock': 'Время',
        'sleep': 'Не мешать', 'wake': 'Разбудить', 'settings': 'Настройки', 'home': 'Домой', 'quit': 'Выйти',
        'convert': 'Конвертировать', 'compress': 'Сжать', 'keep': 'На полку',
        'unkeep': 'Убрать с полки', 'back': 'Назад', 'empty_recent': 'Пока ничего нового',
        'empty_shelf': 'Кидай на меня файлы, я их тут сохраню', 'ui_lang': 'Интерфейс',
        'speech_lang': 'Реплики', 'reactions': 'Реакции на систему', 'chatter': 'Болтовня', 'pinned': 'Не прятаться',
        'on': 'вкл', 'off': 'выкл', 'colors': 'Цвета', 'to': 'в', 'working': 'Работаю',
        'open': 'Открыть', 'files': 'файлов: {n}', 'cancel': 'Отмена',
        'p_small': 'Поменьше', 'p_1mb': 'До 1 МБ', 'p_half': 'В два раза меньше',
        'p_10': 'До 10 МБ', 'p_25': 'До 25 МБ', 'p_50': 'До 50 МБ',
        'p_720': '720p', 'p_480': '480p', 'p_opus': 'Opus 96k', 'p_mp3': 'MP3 128k',
        'p_lossless': 'Без потерь',
        'convert_to': 'Конвертировать в', 'compress_how': 'Сжать', 'follow': 'Цвета от обоев',
        'timer': 'Таймер', 'pause': 'Пауза', 'resume': 'Старт', 'reset': 'Сброс', 'stop': 'Стоп', 'picked': 'Пипетка',
        'timer_sound': 'Звук таймера', 'sound_default': '8-бит', 'sound_reset': 'Вернуть стандартный',
        'sound_test': 'Прослушать', 'audio_files': 'Звуки',
        'hint': 'Перетащи файл на меня: сконвертирую, сожму или сохраню',
        'breaks': 'Перерывы', 'idle_nap': 'Дремать без тебя', 'outfit': 'Наряд',
        'voice': 'Голосок', 'voice_kind': 'Тип голоска', 'v_blips': 'Пищалка', 'v_tts': 'Настоящий',
        'cat_her': 'Поведение', 'cat_sound': 'Звук', 'cat_look': 'Внешний вид', 'cat_lang': 'Язык', 'n_a': 'пока недоступно', 'o_auto': 'авто', 'o_off': 'нет', 'o_halloween': 'Хэллоуин', 'o_newyear': 'Новый год',
        'hourly_chimes': 'Каждый час она говорит время',
        'o_valentine': 'Валентинка', 'o_spring': '8 Марта', 'min': 'мин', 'monitor': 'Экран',
        'cat_ring': 'Кнопки', 'ring_done': 'Готово', 'ring_new': 'Новая кнопка', 'ring_change': 'Изменить кнопку', 'ring_save': 'Сохранить',
        'ring_cmd': 'Команда', 'ring_name': 'Название кнопки', 'ring_icon': 'Иконка', 'ring_file': 'Своя картинка...',
        'ring_add': 'Добавить', 'images': 'Картинки',
        'restart': 'Перезапустить', 'tour_again': 'Показать знакомство', 'tour_next': 'Дальше', 'tour_skip': 'Пропустить', 'tour_done': 'Поняла!',
        'tour_title': 'Привет!',
        'listen': 'Послушать', 'cat_chat': 'Её мозги', 'chat_model': 'Модель ответов',
        'joke': 'Анекдот',
        'chat_refresh': 'Найти модель',
        'wake_ears': 'Будить по «Юми»', 'stt_size': 'Точность ушей', 'e_small': 'Лёгкая',
    'music_skill': 'Умение управлять музыкой',
        'e_big': 'Точная (скачает ~1.8 ГБ)', 'cat_ears': 'Её ушки', 'stt_mic': 'Микрофон',
        'wake_sensitivity': 'Строгость пробуждения', 'wake_sensitivity_normal': 'Обычная',
        'wake_sensitivity_strict': 'Строгая', 'wake_confirm': 'Проверять судом',
        'wake_model_size': 'Модель пробуждения', 'wake_judge_log': 'Лог суда в /tmp/yumi',
        'mic_refresh': 'Найти микрофон', 'mic_auto': 'авто',
        'chat_provider': 'Провайдер мозгов', 'polza_url': 'Адрес Polza.ai', 'polza_api_key': 'Ключ Polza.ai',
        'tap_ears': 'Дневник ушек (/tmp/yumi)', 'ears_range': 'Дальность ушек',
        'r_near': 'Близко', 'r_mid': 'Обычно', 'r_far': 'Через комнату',
        'hourly_chimes': 'Каждый час она говорит время',
        'tour1': 'Привет! Я Юми. Я живу у нижнего края твоего экрана. Давай я всё покажу.',
        'tour2': 'Нажми на меня, и вокруг появятся кружочки: файлы, полка, пипетка, таймер, «Не мешать» и настройки.',
        'tour3': 'Покрути колёсико над мной, и появится таймер. Кликни по цифрам, чтобы вписать время.',
        'tour4': 'Кидай на меня файлы, и я их сконвертирую или сожму. Возьми меня за шкирку, чтобы поставить где угодно.',
        'tour5': 'Средний клик закрепляет меня, чтобы я не пряталась. Остальное в шестерёнке: язык, цвета, наряды.',
        'B': 'Б', 'KB': 'КБ', 'MB': 'МБ', 'GB': 'ГБ',
    },
}


def tr(settings, key, **kw):
    lang = settings.get('ui_lang', 'en')
    text = UI.get(lang, UI['en']).get(key) or UI['en'].get(key, key)
    return text.format(**kw) if kw else text
