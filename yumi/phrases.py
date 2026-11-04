"""What Yumi says, and when she is allowed to say it (pure Python, no GTK).

Every event has 3-5 lines per language: (text, face). Faces are the standing face overlays:
happy, surprised, shy, annoyed, sleeping, wink, worried, or None for her usual sleepy look.
Placeholders: {file} {saved} {hex} {pct} {ssid} {fmt} {n}.

Rules (from the plan):
  * answers to what the user did ("action") are always spoken;
  * system reactions ("system") at most once every 2.5 minutes, low battery always gets through;
  * idle chatter ("chatter") at most once every 10 minutes and only while she is already out;
  * nothing while she sleeps (Do Not Disturb) except the wake-up line;
  * never the same line as one of the last three for that event.
  * no sleep-shaming, no remarks about the time of day.
"""
import random
import time
from collections import deque

SYSTEM_GAP = 150.0
CHATTER_GAP = 600.0
BYPASS = {'battery_low'}

LINES = {
    'en': {
        'hello': [("Mm... I'm up. Kind of.", None), ("Hi. I'll be in the corner.", 'happy'),
                  ("Morning... or whatever time it is.", None), ("Present. Mostly.", 'wink')],
        'pickup': [("Hey! Not by the collar...", 'surprised'), ("Waah, put me down gently!", 'worried'),
                   ("I'm not a kitten, you know.", 'annoyed'), ("Whoa, flying lessons?", 'surprised')],
        'dock': [("Phew. Safe in my corner.", 'happy'), ("Ahh, the edge. Much better.", 'happy'),
                 ("Home sweet bottom edge.", 'wink'), ("Okay, I can hide here.", None)],
        'air': [("W-wait, there's nowhere to hide here...", 'shy'),
                ("Everyone can see me up here!", 'shy'),
                ("Um. I'll just... flap. Awkwardly.", 'shy'),
                ("Can I go back down? Please?", 'worried')],
        'pet': [("Hehe... that tickles.", 'shy'), ("Mm. Okay. A little more.", 'happy'),
                ("Don't stop. I mean... you can stop.", 'shy'), ("My hair is going to be a mess.", 'wink')],
        'nap': [("Nobody's here. Nap time.", 'sleeping'), ("*yawn* I'll just rest my eyes...", 'sleeping'),
                ("Wake me when you're back.", 'sleeping')],
        'nap_wake': [("Oh! You're back.", 'surprised'), ("I wasn't sleeping. Okay, I was.", 'shy'),
                     ("Mm... hi.", 'happy')],
        'break_water': [("Time for some water.", 'happy'), ("Drink something. I'll wait.", None),
                        ("Water break! Go go go.", 'wink')],
        'break_eyes': [("Look far away for 20 seconds. Out the window, maybe.", None),
                       ("Eye break: look at something far away.", 'happy'),
                       ("Your eyes need a rest. Look away for a bit.", None)],
        'break_stretch': [("Stand up and stretch. Shoulders too.", 'happy'),
                          ("You've been sitting a while. Stretch!", None),
                          ("Roll your shoulders. Yes, now.", 'wink')],
        'pin_on': [("Okay, I'll stay right here.", 'happy'), ("Not hiding anymore.", 'wink'),
                   ("Staying out. Don't make it weird.", 'shy')],
        'pin_off': [("Back to hiding when you're not looking.", 'wink'), ("Okay, I can go and hide again.", None),
                    ("Hiding is allowed again.", 'happy')],
        'voice_test': [("Is this loud enough? Too loud?", None), ("Testing. One, two. Moth.", 'happy'),
                       ("Like this? I can be quieter.", 'shy')],
        'home': [("Going home~", 'happy'), ("Back to my corner.", None),
                 ("Wheee. Home.", 'happy')],
        'open': [("Opening {file}.", None), ("Here's {file}.", 'happy'),
                 ("{file}, coming up.", None)],
        'drag_out': [("Take it, it's yours.", 'happy'), ("Careful with that one.", 'wink'),
                     ("Off you go, little file.", None)],
        'shelf_add': [("I'll hold on to {file}.", 'happy'), ("{file} is on my shelf now.", 'wink'),
                      ("Kept. I won't lose it. Probably.", None),
                      ("Shelf secured.", 'happy')],
        'shelf_remove': [("Okay, letting {file} go.", None), ("Off the shelf.", None),
                         ("Bye, {file}.", 'wink')],
        'drop': [("Ooh, what do we do with {file}?", 'surprised'),
                 ("A file! For me?", 'happy'), ("What should I do with this?", None),
                 ("Got it. Now what?", None)],
        'drop_many': [("{n} files at once? Okay...", 'surprised'),
                      ("That's a whole pile. What now?", 'worried'),
                      ("{n} of them. Let's go.", None)],
        'job_start': [("On it.", None), ("Working... don't watch.", 'shy'),
                      ("Give me a sec.", None), ("Mm, crunching.", None)],
        'convert_done': [("Done! {file} is ready.", 'happy'), ("Here, fresh {fmt}.", 'wink'),
                         ("Converted. That was easy.", 'happy'), ("{file}, all done.", None)],
        'compress_done': [("Squished it by {saved}!", 'happy'), ("{saved} lighter. Nice.", 'wink'),
                          ("Down {saved}. Tiny file energy.", 'happy'),
                          ("Done, {saved} smaller.", None)],
        'compress_none': [("It's already as small as it gets.", 'worried'),
                          ("Couldn't make it smaller, sorry.", 'worried'),
                          ("That one was already tiny.", None)],
        'job_fail': [("Oops... that didn't work.", 'worried'),
                     ("Something broke. Not me, I hope.", 'worried'),
                     ("Sorry, I couldn't do it.", 'worried')],
        'missing': [("I need {fmt} for that. Is it installed?", 'worried'),
                    ("Can't, {fmt} is missing.", 'worried')],
        'pipette_start': [("Click any pixel!", 'happy'), ("Point me at a color.", None),
                          ("Pipette ready~", 'wink')],
        'pipette_done': [("{hex}! Copied.", 'happy'), ("Got it: {hex}.", 'wink'),
                         ("{hex}, in your clipboard.", None), ("Pretty. {hex}.", 'happy')],
        'copied': [("Copied {hex}.", None), ("{hex} is in your clipboard.", 'happy')],
        'sleep': [("Do not disturb... zzz.", 'sleeping'), ("Wake me if it's important.", 'sleeping'),
                  ("Blanket mode.", 'sleeping'), ("Nap time. Shh.", 'sleeping')],
        'wake': [("Mmh? I'm awake. I'm awake.", 'surprised'), ("Five more minutes... okay, fine.", None),
                 ("Huh? Oh, hi.", 'surprised'), ("I wasn't sleeping. Just resting my eyes.", 'wink')],
        'music': [("Oh, this is a good one.", 'happy'), ("I can't sit still to this.", 'happy'),
                  ("Okay, this has a nice beat.", 'wink'), ("Who's playing this? I like it.", 'happy')],
        'charger_on': [("Charging. Yum.", 'happy'), ("Power! Thank you.", 'happy'),
                       ("Plugged in, cozy.", None)],
        'charger_off': [("Unplugged... I'll be careful.", None), ("Running on battery now.", None),
                        ("Okay, no cable. Brave.", 'wink')],
        'battery_low': [("Battery's at {pct}%... a charger, maybe?", 'worried'),
                        ("Only {pct}% left!", 'worried'), ("We're running low, {pct}%.", 'worried')],
        'wifi_on': [("Back online: {ssid}.", 'happy'), ("Wi-Fi's here. {ssid}.", None),
                    ("Connected to {ssid}.", None)],
        'wifi_off': [("Wi-Fi's gone...", 'worried'), ("We're offline.", 'worried'),
                     ("No internet. Cozy, kind of.", None)],
        'download': [("{file} finished downloading.", 'happy'), ("Download's done: {file}.", None),
                     ("New file! {file}.", 'surprised'), ("{file} landed.", 'wink')],
        'screenshot': [("Click! Saved.", 'wink'), ("Nice shot.", 'happy'),
                       ("Screenshot's in your pictures.", None)],
        'chatter': [("Moths are just night butterflies with better taste.", 'wink'),
                    ("I like it down here. It's quiet.", None),
                    ("Do you think the cursor gets tired?", None),
                    ("My antennae are picking up... nothing. Good.", None),
                    ("If you need me, I'm right here. Being small.", 'happy'),
                    ("Tux says hi.", 'wink')],
        'pile_empty': [("Nothing new yet.", None), ("All quiet here.", None)],
        'timer_done': [("Time's up!", 'surprised'), ("Ding! The timer's done.", 'happy'),
                       ("Hey, that's {mins}. Time!", 'surprised'), ("Timer's over~", 'wink')],
    },
    'ru': {
        'hello': [("Мм... я тут. Почти.", None), ("Привет. Я в уголке.", 'happy'),
                  ("Доброе... что-нибудь.", None), ("На месте. В основном.", 'wink')],
        'pickup': [("Эй! Не за шкирку же...", 'surprised'), ("Ааа, аккуратнее!", 'worried'),
                   ("Я тебе не котёнок вообще-то.", 'annoyed'), ("Ого, уроки полёта?", 'surprised')],
        'dock': [("Фух. Я в своём уголке.", 'happy'), ("Ааа, край. Так лучше.", 'happy'),
                 ("Родной нижний край.", 'wink'), ("Ладно, тут можно спрятаться.", None)],
        'air': [("П-подожди, тут негде спрятаться...", 'shy'), ("Меня же тут все видят!", 'shy'),
                ("Эм. Я просто... помашу крылышками.", 'shy'),
                ("Можно мне обратно вниз? Ну пожалуйста?", 'worried')],
        'pet': [("Хихи... щекотно.", 'shy'), ("Мм. Ладно. Ещё чуть-чуть.", 'happy'),
                ("Не останавливайся. То есть... можно остановиться.", 'shy'), ("Причёска теперь вся растрёпана.", 'wink')],
        'nap': [("Никого нет. Сон-час.", 'sleeping'), ("*зевок* Только на секундочку прикрою глаза...", 'sleeping'),
                ("Разбуди, когда вернёшься.", 'sleeping')],
        'nap_wake': [("О! Ты вернулся.", 'surprised'), ("Я не спала. Ладно, спала.", 'shy'),
                     ("Мм... привет.", 'happy')],
        'break_water': [("Пора выпить воды.", 'happy'), ("Выпей чего-нибудь. Я подожду.", None),
                        ("Перерыв на воду! Давай-давай.", 'wink')],
        'break_eyes': [("Посмотри вдаль секунд двадцать. В окно, например.", None),
                       ("Перерыв для глаз: посмотри на что-нибудь далёкое.", 'happy'),
                       ("Глазам нужен отдых. Отвлекись ненадолго.", None)],
        'break_stretch': [("Встань и потянись. Плечи тоже.", 'happy'),
                          ("Ты давно сидишь. Потянись!", None),
                          ("Покрути плечами. Да, прямо сейчас.", 'wink')],
        'pin_on': [("Ладно, остаюсь тут.", 'happy'), ("Больше не прячусь.", 'wink'),
                   ("Остаюсь. Только не смотри так.", 'shy')],
        'pin_off': [("Снова прячусь, когда не смотришь.", 'wink'), ("Ладно, можно снова прятаться.", None),
                    ("Прятаться снова можно.", 'happy')],
        'voice_test': [("Так слышно? Не слишком громко?", None), ("Проверка. Раз, два. Мотылёк.", 'happy'),
                       ("Вот так? Могу тише.", 'shy')],
        'home': [("Домой~", 'happy'), ("Обратно в уголок.", None), ("Ура. Домой.", 'happy')],
        'open': [("Открываю {file}.", None), ("Вот {file}.", 'happy'), ("{file}, держи.", None)],
        'drag_out': [("Забирай, он твой.", 'happy'), ("Аккуратнее с ним.", 'wink'),
                     ("Лети, файлик.", None)],
        'shelf_add': [("Подержу {file} у себя.", 'happy'), ("{file} теперь на полке.", 'wink'),
                      ("Сохранила. Не потеряю. Наверное.", None), ("Полка, готово.", 'happy')],
        'shelf_remove': [("Ладно, отпускаю {file}.", None), ("Убрала с полки.", None),
                         ("Пока, {file}.", 'wink')],
        'drop': [("Ой, а что делаем с {file}?", 'surprised'), ("Файлик! Мне?", 'happy'),
                 ("Что с этим сделать?", None), ("Поймала. А дальше?", None)],
        'drop_many': [("Сразу {n} файлов? Ну ладно...", 'surprised'),
                      ("Целая куча. Что делаем?", 'worried'), ("{n} штук. Поехали.", None)],
        'job_start': [("Делаю.", None), ("Работаю... не смотри.", 'shy'), ("Секундочку.", None),
                      ("Мм, перемалываю.", None)],
        'convert_done': [("Готово! {file} ждёт.", 'happy'), ("Держи, свеженький {fmt}.", 'wink'),
                         ("Сконвертировала. Это было легко.", 'happy'), ("{file}, всё.", None)],
        'compress_done': [("Сжала на {saved}!", 'happy'), ("Легче на {saved}. Класс.", 'wink'),
                          ("Минус {saved}. Крошечный файлик.", 'happy'),
                          ("Готово, меньше на {saved}.", None)],
        'compress_none': [("Он уже и так меньше некуда.", 'worried'),
                          ("Не получилось сделать меньше, прости.", 'worried'),
                          ("Этот и так крошечный.", None)],
        'job_fail': [("Ой... не вышло.", 'worried'), ("Что-то сломалось. Надеюсь, не я.", 'worried'),
                     ("Прости, не получилось.", 'worried')],
        'missing': [("Для этого нужен {fmt}. Он установлен?", 'worried'),
                    ("Не могу, нет {fmt}.", 'worried')],
        'pipette_start': [("Кликни на любой пиксель!", 'happy'), ("Покажи мне цвет.", None),
                          ("Пипетка готова~", 'wink')],
        'pipette_done': [("{hex}! Скопировала.", 'happy'), ("Поймала: {hex}.", 'wink'),
                         ("{hex}, уже в буфере.", None), ("Красивый. {hex}.", 'happy')],
        'copied': [("Скопировала {hex}.", None), ("{hex} в буфере.", 'happy')],
        'sleep': [("Не беспокоить... хрр.", 'sleeping'), ("Буди, если что-то важное.", 'sleeping'),
                  ("Режим пледика.", 'sleeping'), ("Тихий час. Тшш.", 'sleeping')],
        'wake': [("Мм? Я не сплю. Не сплю.", 'surprised'), ("Ещё пять минуточек... ладно.", None),
                 ("А? О, привет.", 'surprised'), ("Я не спала. Просто глазки отдыхали.", 'wink')],
        'music': [("О, а это классная.", 'happy'), ("Под такое невозможно сидеть спокойно.", 'happy'),
                  ("Хороший ритм, мне нравится.", 'wink'), ("Что это играет? Мне нравится.", 'happy')],
        'charger_on': [("Заряжаемся. Ням.", 'happy'), ("Энергия! Спасибо.", 'happy'),
                       ("Подключили, уютно.", None)],
        'charger_off': [("Отключили... буду экономной.", None), ("Теперь на батарейке.", None),
                        ("Без провода. Смело.", 'wink')],
        'battery_low': [("Батарейка {pct}%... может, зарядку?", 'worried'),
                        ("Осталось всего {pct}%!", 'worried'), ("Садимся, {pct}%.", 'worried')],
        'wifi_on': [("Снова онлайн: {ssid}.", 'happy'), ("Wi-Fi вернулся. {ssid}.", None),
                    ("Подключились к {ssid}.", None)],
        'wifi_off': [("Wi-Fi пропал...", 'worried'), ("Мы офлайн.", 'worried'),
                     ("Нет интернета. Даже уютно.", None)],
        'download': [("{file} скачался.", 'happy'), ("Загрузка готова: {file}.", None),
                     ("Новый файл! {file}.", 'surprised'), ("{file} приземлился.", 'wink')],
        'screenshot': [("Щёлк! Сохранила.", 'wink'), ("Хороший кадр.", 'happy'),
                       ("Скриншот в картинках.", None)],
        'chatter': [("Мотыльки это ночные бабочки со вкусом.", 'wink'),
                    ("Мне тут внизу нравится. Тихо.", None),
                    ("Как думаешь, курсор устаёт?", None),
                    ("Мои антенки ловят... ничего. Это хорошо.", None),
                    ("Если что, я тут. Маленькая.", 'happy'),
                    ("Тукс передаёт привет.", 'wink')],
        'pile_empty': [("Пока ничего нового.", None), ("Тут тихо.", None)],
        'timer_done': [("Время вышло!", 'surprised'), ("Дзинь! Таймер всё.", 'happy'),
                       ("Эй, прошло {mins}. Время!", 'surprised'), ("Таймер закончился~", 'wink')],
    },
}

KIND = {  # which rate limit an event falls under; everything else is an "action"
    'charger_on': 'system', 'charger_off': 'system', 'battery_low': 'system',
    'wifi_on': 'system', 'wifi_off': 'system', 'download': 'system', 'screenshot': 'system',
    'music': 'system', 'chatter': 'chatter',
}


class Speech:
    def __init__(self, settings, clock=time.monotonic, rng=None):
        self.settings = settings
        self.clock = clock
        self.rng = rng or random.Random()
        self.recent = {}
        self.last_system = -1e9
        self.last_chatter = clock()          # no small talk right after start

    def line(self, event, lang=None, **fmt):
        lang = lang or self.settings.get('speech_lang', 'en')
        pool = LINES.get(lang, LINES['en']).get(event) or LINES['en'].get(event)
        if not pool:
            return None
        seen = self.recent.setdefault((lang, event), deque(maxlen=min(3, len(pool) - 1)))
        choices = [i for i in range(len(pool)) if i not in seen] or list(range(len(pool)))
        i = self.rng.choice(choices)
        seen.append(i)
        text, face = pool[i]
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
        return text, face

    def allowed(self, event, out=True, asleep=False):
        kind = KIND.get(event, 'action')
        now = self.clock()
        if asleep and event not in ('wake', 'sleep'):
            return False
        if kind == 'system':
            if not self.settings.get('reactions', True):
                return False
            if event not in BYPASS and now - self.last_system < SYSTEM_GAP:
                return False
        if kind == 'chatter':
            if not self.settings.get('chatter', True) or not out:
                return False
            if now - self.last_chatter < CHATTER_GAP:
                return False
        return True

    def say(self, event, out=True, asleep=False, **fmt):
        """the line to show, or None when the rules say she stays quiet"""
        if not self.allowed(event, out, asleep):
            return None
        kind = KIND.get(event, 'action')
        if kind == 'system':
            self.last_system = self.clock()
        elif kind == 'chatter':
            self.last_chatter = self.clock()
        return self.line(event, **fmt)
