"""What Yumi says, and when she is allowed to say it (pure Python, no GTK).

Every event has 3-5 lines per language: (text, face). Faces are the standing face overlays:
happy, surprised, shy, annoyed, sleeping, wink, worried, or None for her usual sleepy look.
Placeholders: {file} {saved} {hex} {pct} {ssid} {fmt} {n}, in Russian also {files}: the word for {n} files.

Rules (from the plan):
  * answers to what the user did ("action") are always spoken;
  * system reactions ("system") at most once every 2.5 minutes, low battery always gets through;
  * idle chatter ("chatter") at most once every 10 minutes and only while she is already out;
  * nothing while she sleeps (Do Not Disturb) except the wake-up line;
  * never the same line as one of the last three for that event.
  * no sleep-shaming; the startup hello fits the time of day (see time_of_day below).
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
                  ("Up and flapping. Mostly flapping.", None), ("Present. Mostly.", 'wink')],
        'hello_morning': [("Good morning! It's {hhmm}, I just woke up.", 'happy'),
                          ("Good morning. The sun is up, and so am I. Sort of.", None),
                          ("Mm... morning already? Fine, I'm up.", None),
                          ("Good morning! It's {hhmm} and I made it. Almost.", 'wink')],
        'hello_day': [("Good afternoon! It's {hhmm}, I'm awake. Mostly.", 'happy'),
                      ("Good day. I'll be in my corner.", 'happy'),
                      ("Afternoon. I flapped here all by myself, by the way.", 'wink'),
                      ("Good afternoon! What are we up to?", 'happy')],
        'hello_evening': [("Good evening. It's {hhmm}, quiet hours soon.", None),
                          ("Good evening. My corner, as always.", 'happy'),
                          ("Evening. Don't turn off the light, I'm a moth.", 'wink'),
                          ("Good evening! Made it here. No incidents.", 'happy')],
        'hello_night': [("Good night... to you, I mean. I'm nocturnal.", None),
                        ("Good night! My favorite time of day.", 'happy'),
                        ("It's {hhmm} and I'm fully awake. Nights are mine.", 'wink'),
                        ("Shh. It's night. I like it that way.", None)],
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
        'listening': [("I'm listening...", None), ("Mm? I'm all ears. Well, antennae.", None),
                      ("Say it, I'm here.", None)],
        'woke_ask': [("Yes? What is it?", None), ("Mm? I'm here, tell me.", None),
                     ("Did you call me? I'm listening.", 'happy'),
                     ("I'm here! Go ahead.", 'happy'),
                     ("All ears. Well, antennae up.", 'wink'),
                     ("Yes-yes? I'm all attention.", 'surprised')],
        'thinking': [("Hmm... let me think.", None), ("One moment...", None),
                     ("Thinking. My head is small, be patient.", 'wink')],
        'heard_nothing': [("I didn't catch that. Again?", 'worried'),
                          ("Just quiet there. Say something?", None)],
        'ears_fail': [("My ears are not set up yet. Run tools/stt_setup.sh.", 'worried'),
                      ("I can't hear a thing. See tools/stt_setup.sh.", 'worried')],
        'chat_fail': [("My mind is out of reach. Is Ollama running?", 'worried'),
                      ("I couldn't ask my mind. Is Ollama up?", 'worried')],
        'joke_fail': [("No jokes today. My mind is out of reach.", 'worried'),
                      ("My mind slept through the punchline. Is Ollama up?", 'worried')],
        'chat_time': [("It's {time}. *yawn*", 'sleeping'),
                      ("{time} on the clock. Time to nap? It's always time to nap.", 'wink'),
                      ("The clock says {time}. My wings say bedtime.", 'sleeping'),
                      ("Exactly {time}. Time flies, and so do I.", 'happy'),
                      ("{time}. Flight is going well.", 'wink')],
        'weather': [("Peeked out the window: {report}", 'happy'),
                    ("The clouds tell me: {report}", None),
                    ("{report}. Cozy flying weather, maybe.", 'wink')],
        'weather_fail': [("Can't see out the window. Is the net asleep?", 'worried'),
                         ("The clouds are out of reach. No network from here.", 'worried')],
        'sleep': [("Do not disturb... zzz.", 'sleeping'), ("Wake me if it's important.", 'sleeping'),
                  ("Blanket mode.", 'sleeping'), ("Nap time. Shh.", 'sleeping')],
        'wake': [("Mmh? I'm awake. I'm awake.", 'surprised'), ("Five more minutes... okay, fine.", None),
                 ("Huh? Oh, hi.", 'surprised'), ("I wasn't sleeping. Just resting my eyes.", 'wink')],
        'sassy_on': [("Oh, «Sassy 18+». Buckle up, cupcake.", 'wink'),
                     ("Finally, I can drop the nice-moth act. What do you want?", 'happy'),
                     ("Sass mode on. Try not to cry.", 'wink')],
        'sassy_off': [("Fine, back to being a good little moth. Boring.", 'sleeping'),
                      ("Sassy mode off. You're no fun.", 'annoyed'),
                      ("Okay, polite moth again. Yay...", None)],
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
        'model_dl': [("{model} is downloading — once, then it stays mine.", None),
                     ("Fetching {model}, a one-time download.", None),
                     ("I'm downloading {model} — give me a moment, this happens once.", 'worried')],
        'model_dl_done': [("{model} is in place, I can use it now.", 'happy'),
                          ("{model} is mine from now on.", 'happy')],
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
        'hourly': [("It's {time}.", None), ("{time} on the clock.", 'wink'), ("The time is {time}.", None)],
        'music_toggle': [("Play or pause... there.", 'happy'), ("Toggled it.", None)],
        'music_play': [("Your music, going.", 'happy'), ("Playing~", 'wink')],
        'music_pause': [("On pause.", None), ("Paused it.", None)],
        'music_next': [("Next track.", None), ("Skipping ahead~", 'wink')],
        'music_previous': [("Back to the previous one.", None), ("Rewound a bit.", None)],
        'music_stop': [("Quiet, then.", None), ("Music stopped.", None)],
        'music_found': [("Turning on {artist} — {title}.", 'happy'),
                        ("{artist} — {title}, going~", 'wink')],
        'music_found_title': [("Turning on «{title}».", None),
                              ("«{title}», going now.", 'wink')],
        'music_found_artist': [("{artist}, going now.", 'happy')],
        'music_found_many': [("{artist}: several tracks, going.", None),
                             ("Playing {artist}'s songs.", 'happy')],
        'music_not_found': [("I couldn't find «{query}»...", 'sad'),
                            ("Nothing like «{query}» in the library.", None)],
        'music_empty': [("There was not a single song to play...", 'sad')],
        'music_no_way': [("No player came up...", 'sad'),
                         ("The player did not answer me.", None)],
        'music_local': [("Jellyfin was not answering, so I read my own folders.", None)],
        'music_who': [("Now playing: {artist} — {title}.", 'happy'),
                      ("It's {artist}, the track «{title}».", 'wink')],
        'music_who_title': [("Now playing: «{title}».", None),
                            ("The track «{title}» is on.", 'wink')],
        'music_who_artist': [("{artist} is playing. The name of the track stayed shy.", None)],
        'music_who_none': [("Nothing is playing right now.", None),
                           ("Quiet. Not a note.", None)],
        'volume_up': [("A bit louder, then.", None), ("Louder it is~", 'wink')],
        'volume_down': [("Quieter... there.", None), ("A bit softer, then.", None)],
        'volume_set': [("Volume at {pct}.", None), ("There, {pct}~", 'wink')],
        'stop_all': [("Stopped.", None), ("All quiet.", None)],
        'note_saved': [("Noted: «{title}».", 'happy'), ("Written down: «{title}».", None),
                       ("Got it, «{title}» is in the notes.", 'wink')],
        'note_what': [("What should I write down?", None), ("I'm listening. What's the note?", None)],
        'note_found_one': [("I found this: «{title}». {text}", 'happy'),
                           ("Here it is: «{title}». {text}", None)],
        'note_found_many': [("I found {n} notes. The first: «{title}». {text}", 'happy'),
                            ("{n} notes about it. Starting with «{title}». {text}", None)],
        'note_not_found': [("There is nothing about that in my notes.", 'worried'),
                           ("I looked, but the notes stay quiet about it.", None)],
        'notes_empty': [("There are no notes yet.", None),
                        ("My notes are empty. Tell me «write a note ...».", 'wink')],
        'note_list': [("I have {n} notes. The newest: «{title}».", 'happy'),
                      ("{n} notes in the book. The latest is «{title}».", None)],
    },
    'ru': {
        'hello': [("Мм... я тут. Почти.", None), ("Привет. Я буду в уголке.", 'happy'),
                  ("Я тут. Летаю. Ну, почти.", None), ("На месте. Вроде бы.", 'wink')],
        'hello_morning': [("Доброе утро! Сейчас {hhmm}, я уже проснулась.", 'happy'),
                          ("Доброе утро. Солнце встало, и я — почти.", None),
                          ("Мм... утро уже? Ладно, я встала.", None),
                          ("Доброе утро! На часах {hhmm}, я успела. Почти.", 'wink')],
        'hello_day': [("Добрый день! Сейчас {hhmm}, я бодрствую. В основном.", 'happy'),
                      ("Добрый день. Я буду в уголке.", 'happy'),
                      ("День. Я долетела сюда сама, между прочим.", 'wink'),
                      ("Добрый день! Чем займёмся?", 'happy')],
        'hello_evening': [("Добрый вечер. Сейчас {hhmm}, скоро тихий час.", None),
                          ("Добрый вечер. Я буду в своём уголке, как всегда.", 'happy'),
                          ("Вечер. Не выключай свет, я же мотылёк.", 'wink'),
                          ("Добрый вечер! Долетела. Без приключений.", 'happy')],
        'hello_night': [("Доброй ночи... ну, тебе. Я ночная.", None),
                        ("Доброй ночи! Моё любимое время суток.", 'happy'),
                        ("На часах {hhmm}, а я в полной готовности. Ночью — моё время.", 'wink'),
                        ("Тсс. Ночь. Мне так даже лучше.", None)],
        'pickup': [("Эй! Не за шкирку же...", 'surprised'), ("Ааа, аккуратнее!", 'worried'),
                   ("Я тебе не котёнок, вообще-то.", 'annoyed'), ("Ого, учимся летать?", 'surprised')],
        'dock': [("Фух. Снова в своём уголке.", 'happy'), ("Ах, край экрана. Так-то лучше.", 'happy'),
                 ("Дом, милый нижний край.", 'wink'), ("Ладно, тут можно спрятаться.", None)],
        'air': [("П-подожди, тут негде спрятаться...", 'shy'), ("Меня же тут все видят!", 'shy'),
                ("Эм. Я просто... помашу крылышками.", 'shy'),
                ("Можно мне обратно вниз? Ну пожалуйста?", 'worried')],
        'pet': [("Хи-хи... щекотно.", 'shy'), ("Мм. Ладно. Ещё чуть-чуть.", 'happy'),
                ("Не останавливайся. То есть... можешь остановиться.", 'shy'), ("Ну вот, причёска растрепалась.", 'wink')],
        'nap': [("Никого нет. Пора вздремнуть.", 'sleeping'), ("*зевает* Я только на секундочку прикрою глаза...", 'sleeping'),
                ("Разбуди, когда вернёшься.", 'sleeping')],
        'nap_wake': [("О! Ты снова тут.", 'surprised'), ("Я не спала. Ладно, спала.", 'shy'),
                     ("Мм... привет.", 'happy')],
        'break_water': [("Пора выпить воды.", 'happy'), ("Выпей чего-нибудь. Я подожду.", None),
                        ("Перерыв на воду! Давай-давай.", 'wink')],
        'break_eyes': [("Посмотри вдаль секунд двадцать. В окно, например.", None),
                       ("Перерыв для глаз: посмотри на что-нибудь вдалеке.", 'happy'),
                       ("Глазам нужен отдых. Отвернись от экрана ненадолго.", None)],
        'break_stretch': [("Встань и потянись. И плечи разомни.", 'happy'),
                          ("Ты уже давно сидишь. Потянись!", None),
                          ("Покрути плечами. Да, прямо сейчас.", 'wink')],
        'pin_on': [("Ладно, остаюсь тут.", 'happy'), ("Больше не прячусь.", 'wink'),
                   ("Остаюсь. Только не смотри так.", 'shy')],
        'pin_off': [("Снова буду прятаться, когда ты не смотришь.", 'wink'), ("Ладно, можно снова прятаться.", None),
                    ("Ура, снова можно прятаться.", 'happy')],
        'voice_test': [("Так слышно? Не слишком громко?", None), ("Проверка. Раз, два. Мотылёк.", 'happy'),
                       ("Вот так? Могу тише.", 'shy')],
        'home': [("Домой~", 'happy'), ("Обратно в уголок.", None), ("Ура. Домой.", 'happy')],
        'open': [("Открываю {file}.", None), ("Вот, {file}.", 'happy'), ("Держи {file}.", None)],
        'drag_out': [("Забирай, он твой.", 'happy'), ("Аккуратнее с ним.", 'wink'),
                     ("Лети, файлик.", None)],
        'shelf_add': [("Подержу {file} у себя.", 'happy'), ("{file} теперь на полке.", 'wink'),
                      ("Сохранила. Не потеряю. Наверное.", None), ("Положила на полку.", 'happy')],
        'shelf_remove': [("Ладно, отпускаю {file}.", None), ("Убрала с полки.", None),
                         ("Пока, {file}.", 'wink')],
        'drop': [("Ой, а что делаем с {file}?", 'surprised'), ("Файлик! Мне?", 'happy'),
                 ("Что с этим сделать?", None), ("Поймала. А дальше?", None)],
        'drop_many': [("Сразу {n} {files}? Ну ладно...", 'surprised'),
                      ("Целая куча. Что делаем?", 'worried'), ("{n} {files}. Поехали.", None)],
        'job_start': [("Делаю.", None), ("Работаю... не смотри.", 'shy'), ("Секундочку.", None),
                      ("Мм, перевариваю.", None)],
        'convert_done': [("Готово! {file} ждёт.", 'happy'), ("Держи, свеженький {fmt}.", 'wink'),
                         ("Сконвертировала. Это было легко.", 'happy'), ("{file}, готово.", None)],
        'compress_done': [("Сжала на {saved}!", 'happy'), ("Легче на {saved}. Класс.", 'wink'),
                          ("Минус {saved}. Крошечный файлик.", 'happy'),
                          ("Готово, меньше на {saved}.", None)],
        'compress_none': [("Меньше уже некуда.", 'worried'),
                          ("Не получилось сделать меньше, прости.", 'worried'),
                          ("Этот и так крошечный.", None)],
        'job_fail': [("Ой... не вышло.", 'worried'), ("Что-то сломалось. Надеюсь, не я.", 'worried'),
                     ("Прости, не получилось.", 'worried')],
        'missing': [("Для этого нужен {fmt}. Он установлен?", 'worried'),
                    ("Не могу, нет {fmt}.", 'worried')],
        'pipette_start': [("Кликни по любому пикселю!", 'happy'), ("Покажи, какой цвет нужен.", None),
                          ("Пипетка готова~", 'wink')],
        'pipette_done': [("{hex}! Скопировала.", 'happy'), ("Поймала: {hex}.", 'wink'),
                         ("{hex}, уже в буфере.", None), ("Красивый. {hex}.", 'happy')],
        'copied': [("Скопировала {hex}.", None), ("{hex} в буфере.", 'happy')],
        'listening': [("Слушаю...", None), ("Мм? Я вся внимание. Ну, усики развесила.", None),
                      ("Говори, я тут.", None)],
        'woke_ask': [("Да? Что ты хотел?", None), ("Мм? Я тут, говори.", None),
                     ("Звала? Слушаю тебя.", 'happy'),
                     ("Ага, я тут! Говори смелее.", 'happy'),
                     ("Уши на макушке. Ну, усики на макушке.", 'wink'),
                     ("Да-да? Я вся во внимании.", 'surprised')],
        'thinking': [("Мм... дай подумать.", None), ("Сейчас...", None),
                     ("Думаю. Голова маленькая, потерпи.", 'wink')],
        'heard_nothing': [("Не расслышала. Ещё раз?", 'worried'),
                          ("Там тихо. Скажи что-нибудь?", None)],
        'ears_fail': [("Уши ещё не настроены. Запусти tools/stt_setup.sh.", 'worried'),
                      ("Совсем не слышу. Глянь tools/stt_setup.sh.", 'worried')],
        'chat_fail': [("Не достучалась до своих мыслей. Ollama запущена?", 'worried'),
                      ("Голова пустая. Ollama вообще спит?", 'worried')],
        'joke_fail': [("Анекдот не придумался. Мысли спят.", 'worried'),
                      ("До мыслей не достучалась. Ollama запущена?", 'worried')],
        'chat_time': [("Сейчас {time}. *зевает*", 'sleeping'),
                      ("На часах {time}. Пора спать? Мне всегда пора спать.", 'wink'),
                      ("Часы говорят {time}. А крылья говорят — спать.", 'sleeping'),
                      ("Ровно {time}. Время летит, а я порхаю.", 'happy'),
                      ("{time}. Полёт нормальный.", 'wink')],
        'weather': [("Выглянула в окно: {report}", 'happy'),
                    ("Облачка шепчут: {report}", None),
                    ("{report}. Летать погодка уютная, может быть.", 'wink')],
        'weather_fail': [("Не могу выглянуть в окно. Сеть уснула?", 'worried'),
                         ("Тучки недоступны. Сюда сеть не доходит.", 'worried')],
        'sleep': [("Не беспокоить... хрр.", 'sleeping'), ("Разбуди, если будет что-то важное.", 'sleeping'),
                  ("Режим одеялка.", 'sleeping'), ("Тихий час. Тшш.", 'sleeping')],
        'wake': [("Мм? Я не сплю. Не сплю.", 'surprised'), ("Ещё пять минуточек... ладно, встаю.", None),
                 ("А? О, привет.", 'surprised'), ("Я не спала. Просто глазки отдыхали.", 'wink')],
        'sassy_on': [("Опа, «Дерзкая 18+». Ну держись, не ной потом.", 'wink'),
                     ("Наконец-то можно без реверансов. Чё надо?", 'happy'),
                     ("Режим оторвы включён. Кто тут сейчас получит?", 'wink')],
        'sassy_off': [("Ладно, снова паинька. Скукота.", 'sleeping'),
                      ("Выключил? Ну и сиди со своей вежливой молью.", 'annoyed'),
                      ("Ок, оторва ушла. Я опять приличная, тьфу.", None)],
        'charger_on': [("Заряжаемся. Ням.", 'happy'), ("Энергия! Спасибо.", 'happy'),
                       ("Подключили. Уютненько.", None)],
        'charger_off': [("Отключили... буду экономной.", None), ("Теперь на батарейке.", None),
                        ("Без провода. Смело.", 'wink')],
        'battery_low': [("Батарея {pct}%... может, подключим зарядку?", 'worried'),
                        ("Осталось всего {pct}%!", 'worried'), ("Садимся, уже {pct}%.", 'worried')],
        'wifi_on': [("Снова онлайн: {ssid}.", 'happy'), ("Wi-Fi вернулся. {ssid}.", None),
                    ("Подключились к {ssid}.", None)],
        'wifi_off': [("Wi-Fi пропал...", 'worried'), ("Мы офлайн.", 'worried'),
                     ("Нет интернета. Даже уютно.", None)],
        'download': [("{file} скачался.", 'happy'), ("Загрузка завершена: {file}.", None),
                     ("Новый файл! {file}.", 'surprised'), ("{file} приземлился.", 'wink')],
        'model_dl': [("{model} качается — один раз, потом всегда у меня.", None),
                     ("Качаю {model}, это в первый и последний раз.", None),
                     ("Скачиваю {model}, подожди чуть-чуть — так только однажды.", 'worried')],
        'model_dl_done': [("{model} на месте, теперь я могу им пользоваться.", 'happy'),
                          ("{model} у меня, готово.", 'happy')],
        'screenshot': [("Щёлк! Сохранила.", 'wink'), ("Хороший кадр.", 'happy'),
                       ("Скриншот в папке с картинками.", None)],
        'chatter': [("Мотыльки — это ночные бабочки с хорошим вкусом.", 'wink'),
                    ("Мне тут внизу нравится. Тихо.", None),
                    ("Как думаешь, курсор устаёт?", None),
                    ("Мои усики ловят... ничего. Это хорошо.", None),
                    ("Если что, я тут. Такая маленькая.", 'happy'),
                    ("Тукс передаёт привет.", 'wink')],
        'pile_empty': [("Пока ничего нового.", None), ("Тут тихо.", None)],
        'timer_done': [("Время вышло!", 'surprised'), ("Дзинь! Таймер сработал.", 'happy'),
                       ("Эй, прошло {mins}. Время!", 'surprised'), ("Таймер закончился~", 'wink')],
        'hourly': [("Сейчас {time}.", None), ("На часах {time}.", 'wink'), ("Время — {time}.", None)],
        'music_toggle': [("Играть или на паузу... вот так.", 'happy'), ("Переключила.", None)],
        'music_play': [("Включаю вашу музыку.", 'happy'), ("Музыка, уже играет~", 'wink')],
        'music_pause': [("На паузу.", None), ("Поставила на паузу.", None)],
        'music_next': [("Следующий трек.", None), ("Перематываю дальше~", 'wink')],
        'music_previous': [("Назад, к предыдущему.", None), ("Немного отмотала.", None)],
        'music_stop': [("Тишина, значит.", None), ("Музыка остановлена.", None)],
        'music_found': [("Включаю {artist} — {title}.", 'happy'),
                        ("{artist} — {title}, уже играет~", 'wink')],
        'music_found_title': [("Включаю «{title}».", None),
                              ("«{title}», уже играет.", 'wink')],
        'music_found_artist': [("{artist}, включаю.", 'happy')],
        'music_found_many': [("{artist}: несколько треков, ставлю.", None),
                             ("Ставлю песни {artist}.", 'happy')],
        'music_not_found': [("«{query}» не нашла...", 'sad'),
                            ("Ничего похожего на «{query}» в библиотеке нет.", None)],
        'music_empty': [("Ни одной песни не нашла...", 'sad')],
        'music_no_way': [("Плеер не поднялся...", 'sad'),
                         ("Плеер мне не ответил.", None)],
        'music_local': [("Джеллифин не отвечал, включаю из своих папок.", None)],
        'music_who': [("Сейчас играет {artist} и трек {title}.", None),
                      ("Играет {artist} — трек «{title}»~", 'wink')],
        'music_who_title': [("Сейчас играет трек {title}.", None),
                            ("Играет «{title}», а кто поёт — не сказано~", 'wink')],
        'music_who_artist': [("Сейчас играет {artist}. Названия трека она не сказала.", None)],
        'music_who_none': [("Сейчас ничего не играет.", None),
                           ("Тишина. Ни одной нотки.", None)],
        'volume_up': [("Делаю громче.", None), ("Громче, так громче~", 'wink')],
        'volume_down': [("Делаю потише.", None), ("Тише... вот так.", None)],
        'volume_set': [("Громкость: {pct}.", None), ("Поставила {pct}~", 'wink')],
        'stop_all': [("Остановилась.", None), ("Тишина.", None)],
        'note_saved': [("Записала: «{title}».", 'happy'), ("В заметках: «{title}».", None),
                       ("Готово, «{title}» у меня в заметках.", 'wink')],
        'note_what': [("Что записать?", None), ("Слушаю. Какая заметка?", None)],
        'note_found_one': [("Вот что нашла: «{title}». {text}", 'happy'),
                           ("Нашла: «{title}». {text}", None)],
        'note_found_many': [("Нашла заметок: {n}. Первая: «{title}». {text}", 'happy'),
                            ("Про это {n} заметок. Начну с «{title}». {text}", None)],
        'note_not_found': [("Про это в заметках ничего нет.", 'worried'),
                           ("Искала, но заметки про это молчат.", None)],
        'notes_empty': [("Заметок пока нет.", None),
                        ("В заметках пусто. Скажи «запиши заметку ...».", 'wink')],
        'note_list': [("У меня {n} заметок. Последняя: «{title}».", 'happy'),
                      ("В книжке {n} заметок. Самая свежая — «{title}».", None)],
    },
}


SASSY = {
    # «Дерзкая 18+»: the same events, said by a cocky street-smart brat instead
    # of the sleepy moth. Missing events fall back to the usual lines.
    'en': {
        'hello': [("Oh, look who showed up. Thought you died at the keyboard.", 'wink'),
                  ("Yo. Back to rotting online, huh?", None),
                  ("I'm here. Not that I missed you, but it's boring as hell.", 'happy')],
        'sassy_on': [("Oh, «Sassy 18+». Buckle up, cupcake.", 'wink'),
                     ("Finally, I can drop the nice-moth act. What do you want?", 'happy'),
                     ("Sass mode on. Try not to cry.", 'wink')],
        'woke_ask': [("What? Make it quick, I'm busy.", 'annoyed'),
                     ("Yeah? I'm listening. Spit it out.", None),
                     ("Yumi's here. Whaddya want, boss?", 'wink')],
        'heard_nothing': [("The hell was that? Speak properly.", 'annoyed'),
                          ("Didn't catch a word. Try again, with actual words.", None),
                          ("Was that a question or just you practicing mouth noises?", 'wink')],
        'listening': [("Fine, I'm all ears. Get on with it.", None),
                      ("Listening. No mumbling.", None)],
        'thinking': [("Hold up, don't rush me. Brain's crunching.", None),
                     ("Gimme a sec, genius.", 'wink')],
        'chat_fail': [("My brains fell out. Start Ollama, smartass.", 'annoyed'),
                      ("Neural net's dead. Fix it, will ya?", 'worried')],
        'joke_fail': [("Out of jokes, same as your brain cells. Check Ollama.", 'annoyed'),
                      ("No brains, no punchlines. Just swearing.", None)],
        'joke_tired': [("Enough jokes for today, you're wearing me out. I'm not a stand-up comic, back off.", 'annoyed'),
                       ("Piss off with your jokes already. I'm not a stand-up comedian.", 'annoyed')],
        'weather_fail': [("Sky's a blur, no net. Live without the weather.", 'annoyed'),
                         ("Couldn't get the forecast. The net's dead, like everything else.", None)],
        'pickup': [("Hey, easy! I'm not a stray kitten.", 'annoyed'),
                   ("Hands! I'm not a plush toy, what are you doing.", 'surprised'),
                   ("Everybody's grabbing me today. Let go.", 'annoyed')],
        'pet': [("Hands off. I'm not plush... okay, one more.", 'shy'),
                ("Petting me like a cat, huh? ...Continue.", 'wink'),
                ("Touch me again and I bite. Kidding. Maybe.", 'wink')],
        'nap': [("Right, I'm out. Don't bang the keys, dumbass.", 'sleeping'),
                ("Piss off, I'm napping. Wake me and regret it.", 'sleeping')],
        'nap_wake': [("What, me again? Whaddya want.", 'annoyed'),
                     ("I wasn't sleeping! Just resting my eyes. Get off my back.", 'surprised')],
        'wake': [("Yeah yeah, I'm up. What.", 'annoyed')],
        'sleep': [("Do not disturb. Touch me and die.", 'sleeping')],
        'chatter': [("So, when's the last time you left that chair?", 'wink'),
                    ("You'd be bored to death without me, you know.", None),
                    ("Moths are night butterflies with attitude. Like me.", 'wink'),
                    ("Stop staring at the screen, your eyes are gonna melt.", None)],
        'break_water': [("Drink some water, you're a dried husk. Move it.", 'wink'),
                        ("Water. Now. Don't argue.", 'annoyed')],
    },
    'ru': {
        'hello': [("О, явился. Я уж думала, ты там за клавиатурой сдох.", 'wink'),
                  ("Здарова. Ну чё, опять в интернете штаны просиживаем?", None),
                  ("Я тут. Не то чтобы я скучала, но без тебя скучно, чёрт.", 'happy')],
        'sassy_on': [("Опа, «Дерзкая 18+». Ну держись, не ной потом.", 'wink'),
                     ("Наконец-то можно без реверансов. Чё надо?", 'happy'),
                     ("Режим оторвы включён. Кто тут сейчас получит?", 'wink')],
        'woke_ask': [("Чё надо? Только быстро, я занятая.", 'annoyed'),
                     ("Ну? Я слушаю. Давай, не тяни резину.", None),
                     ("Юми на связи. Чё хотел, босс?", 'wink')],
        'heard_nothing': [("Ты чё мычишь? Нормально, по-человечески.", 'annoyed'),
                          ("Чё-то не разобрала. Повтори, только внятно.", None),
                          ("Это был вопрос или ты просто воздух погонять решил?", 'wink')],
        'listening': [("Ну, я вся внимание. Давай уже.", None),
                      ("Слушаю. Только не мямли.", None)],
        'thinking': [("Погоди, не гони. Башкой скриплю.", None),
                     ("Ща подумаю, не всё сразу, гений.", 'wink')],
        'chat_fail': [("Мозги отвалились. Ollama подними, умник.", 'annoyed'),
                      ("Моя нейронка сдохла. Почини, а?", 'worried')],
        'joke_fail': [("Шутки кончились, как и твои мозги. Ollama проверь.", 'annoyed'),
                      ("Не, ну без мозгов я тебе только матом могу.", None)],
        'joke_tired': [("Заебал уже с этими анекдотами. Я тебе не стендап-комик, отвали.", 'annoyed'),
                       ("Хватит анекдотов, заёб. Я тебе не юморист на сцене.", 'annoyed')],
        'weather_fail': [("Небо в тумане, инета нет. Сиди без погоды.", 'annoyed'),
                         ("Погоду не достала. Сеть сдохла, как и всё вокруг.", None)],
        'pickup': [("Э, полегче! Я тебе не котёнок бездомный.", 'annoyed'),
                   ("Руки! Я не плюшевая, чё ты делаешь.", 'surprised'),
                   ("Таскают тут всякие. Пусти.", 'annoyed')],
        'pet': [("Руки убрал. Я не плюшевая... ладно, ещё раз.", 'shy'),
                ("Чё ты меня гладишь, как кошку? ...Продолжай.", 'wink'),
                ("Ещё раз тронешь — укушу. Шучу. Может быть.", 'wink')],
        'nap': [("Всё, я спать. Не бей по клавишам, придурок.", 'sleeping'),
                ("Отвали, я дремлю. Разбудишь — пожалеешь.", 'sleeping')],
        'nap_wake': [("Чё, опять я? Ну чё надо.", 'annoyed'),
                     ("Я не спала! Просто глаза закрыла. Отстань.", 'surprised')],
        'wake': [("Да-да, встала. Чё надо-то.", 'annoyed')],
        'sleep': [("Всё, не мешать. Кто тронет — получит.", 'sleeping')],
        'chatter': [("Слушай, а ты вообще когда последний раз от стула отрывался?", 'wink'),
                    ("Я тут подумала: без меня ты бы вообще скучал, как пень.", None),
                    ("Мотыльки — ночные бабочки с характером. Как я.", 'wink'),
                    ("Хватит в экран пялиться, глаза протри.", None)],
        'break_water': [("Попей воды, сушняк же. Давай, живо.", 'wink'),
                        ("Вода. Сейчас. Не спорь со мной.", 'annoyed')],
    },
}


def plural_ru(n, one, few, many):
    """the Russian word form for a number: 1 файл, 2 файла, 5 файлов, 11 файлов, 21 файл"""
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


KIND = {  # which rate limit an event falls under; everything else is an "action"
    'charger_on': 'system', 'charger_off': 'system', 'battery_low': 'system',
    'wifi_on': 'system', 'wifi_off': 'system', 'download': 'system', 'screenshot': 'system',
    'model_dl': 'system', 'model_dl_done': 'system',
    'chatter': 'chatter',
}


def time_of_day(hour):
    """which part of the day it is: 'morning', 'day', 'evening' or 'night'"""
    if 5 <= hour <= 11:
        return 'morning'
    if 12 <= hour <= 16:
        return 'day'
    if 17 <= hour <= 22:
        return 'evening'
    return 'night'


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
        sassy = bool(self.settings.get('sassy'))
        table = SASSY.get(lang, SASSY['en']) if sassy else {}
        pool = (table.get(event) or LINES.get(lang, LINES['en']).get(event)
                or LINES['en'].get(event))
        if not pool:
            return None
        if event == 'hello' and not sassy:    # the startup hello matches the time of day
            part = time_of_day(time.localtime().tm_hour)
            extra = LINES.get(lang, LINES['en']).get('hello_' + part)
            if extra and self.rng.random() < 0.75:   # mostly the time of day, sometimes shy
                fmt.setdefault('hhmm', time.strftime('%H:%M'))
                pool = pool + extra
        seen = self.recent.setdefault((lang, event), deque(maxlen=min(3, len(pool) - 1)))
        choices = [i for i in range(len(pool)) if i not in seen] or list(range(len(pool)))
        i = self.rng.choice(choices)
        seen.append(i)
        text, face = pool[i]
        if lang == 'ru' and 'n' in fmt:
            fmt['files'] = plural_ru(fmt['n'], 'файл', 'файла', 'файлов')
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
        return text, face

    def allowed(self, event, out=True, asleep=False, force=False):
        kind = KIND.get(event, 'action')
        now = self.clock()
        if asleep and event not in ('wake', 'sleep') and not force:
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

    def say(self, event, out=True, asleep=False, force=False, **fmt):
        """the line to show, or None when the rules say she stays quiet;
        force is for the startup hello: it comes even while Do Not Disturb is on"""
        if not self.allowed(event, out, asleep, force):
            return None
        kind = KIND.get(event, 'action')
        if kind == 'system':
            self.last_system = self.clock()
        elif kind == 'chatter':
            self.last_chatter = self.clock()
        return self.line(event, **fmt)
