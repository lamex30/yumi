# Handoff: выбор активации, судьи и голоса (Юми + веб-панель)

> Документ передачи работы. Обновляется **в одном коммите с кодом**.
> Следующий агент начинает с раздела «Статус» и «Как подхватить» и не
> переоткрывает уже сделанное.

---

## 0. Как подхватить работу

1. `git branch --show-current` — работа идёт в `yumi-wake-engineb`
   (ветка wake-движка), либо в новой ветке `yumi-voice-controls`.
2. `git status` — код и этот документ меняются вместе.
3. Прогнать проверки из раздела 6. Они должны быть зелёными до и после
   изменений.
4. Брать задачи из раздела «Осталось сделать» сверху вниз.

---

## 1. Задача (кратко)

В Юми и в браузерной панели должны быть **выбор и проверка вживую**:

1. **Активация** — какое ухо ловит имя «Юми»: `openWakeWord` (обученная
   модель `yumi.onnx`) или `vosk` (как раньше). Плюс кнопка «проверить имя»
   в панели: сказать «Юми», получить «услышала / тишина» и понятную причину.
2. **Судья / распознаватель фраз** — кто читает фразу после имени:
   `whisper` или `nemotron 0.6b`. Плюс кнопка «проверить судью»: сказать
   фразу, увидеть, что именно прочитал выбранный движок.
3. **Голос озвучки** — русский и английский голоса Silero, модель (`v5_6_ru`
   / `v4_ru`), высота (`pitch`) и существующая громкость. Плюс кнопка
   «послушать» с текущими параметрами, без перезапуска приложения.

Всё это уже частично есть в `settings.json` (`wake_ear`, `stt_engine`), но
TTS-профиль жил только в `~/.config/yumi/tts.json`, а проверок в панели не
было. Задача — свести три ручки в один понятный пульт и не сломать ручное
редактирование `tts.json`.

---

## 2. Статус

### ✅ Сделано

- [x] исследование кода: `wake_ear` и `stt_engine` уже есть в
      `DEFAULT_SETTINGS`, TTS-профиль читался только демоном из
      `tts.json`, панель умела `listen/stop/speak`, но не умела проверять
      именно активацию/судью/голос;
- [x] этот документ и разбивка задачи (раздел 4);
- [x] **1. Слой настроек TTS** — ключи `tts_ru_speaker`, `tts_en_speaker`,
      `tts_ru_model`, `tts_pitch` в `yumi/config.py`, подписи ru/en;
      `load_settings()` подтягивает ручной `tts.json` через
      `_adopt_tts_profile`; каталог панели дополнен;
- [x] **2. Живое применение TTS** — `tts.sync_profile` пишет `tts.json` и
      перезапускает демон только при реальной смене профиля; вызывается из
      `App.handle_requests` (GTK) и `App._web_apply_settings` (панель);
- [x] **3. Движки и проверки** — `yumi/engines.py` отдаёт наличие
      oww/vosk/whisper/nemotron/TTS и списки голосов; в мосте
      `voice_engines`, `voice_test_wake`, `voice_listen(engine=...)`,
      `voice_test`; фоновый wake-цикл ставится на паузу через
      `_ears_exclusive` и всегда возобновляется;
- [x] **4. API** — `GET /api/voice/engines`, `POST /api/voice/test-wake`,
      `POST /api/voice/test`; `POST /api/voice/listen` принимает `engine`;
- [x] **5. Страница `voice`** — карточки «Активация», «Судья фраз»,
      «Голос» с выбором, бейджами доступности и кнопками проверки; ниже
      остались прежние настройки ушей/звука;
- [x] **6. Паритет GTK** — строки TTS-голоса/высоты/модели в
      «Настройки → Звук», циклы в `Brain.cycles`, подписи ru/en;
- [x] **7. Тесты и документация** — `tests/test_webpanel.py` (схема, роуты,
      заглушки), `tests/test_tts.py` (профиль и перезапуск), `tests/test_voice_preview.py`
      (проба не глушится кадром, как её обычная речь), `README.md`,
      `README.ru.md`, `docs/handoff-web-panel.md`.

### 🔜 Осталось сделать

- [ ] **8. Живая приёмка** — вживую: «Юми» ловится выбранным ухом, фраза
      читается выбранным судьёй, голос звучит с выбранным тембром/высотой.
      Шаги — в разделе 6; автоматические проверки уже зелёные.

> Исправлено: проба голоса из панели больше не обрывается сразу после старта.
> У неё нет своего пузыря, поэтому кадр `App.frame` глушил её как обычную речь;
> теперь `voice_preview` помечает такую пробу и кадр её не трогает. Если плеер
> не нашёлся, мост возвращает ошибку, а не мнимое «сказала».

### Известные ограничения окружения

- `pytest tests/ -q` не собирает старые standalone-скрипты (они вызывают
  `sys.exit(0)`), поэтому тесты запускаются по одному.
- `tests/test_wake.py` падает, если в локальном
  `~/.config/yumi/settings.json` стоит нестандартный `phrase_tail_ms`:
  тест ждёт значение по умолчанию 1200. К этой задаче отношения не имеет.

---

## 3. Архитектура (как это устроено сейчас)

```
settings.json
  wake_ear: auto | oww | vosk       -> ui/senses, ui/brain -> ears.wake(oww_ear=...)
  stt_engine: whisper | nemotron    -> ears.listen(engine=...) / wake -> STT
  voice_kind: blips | tts           -> senses.speak -> tts.speak
  voice_vol: 0..100                 -> громкость
  tts_ru_speaker, tts_en_speaker,
  tts_ru_model, tts_pitch           -> tts.sync_profile -> tts.json + рестарт демона

~/.config/yumi/tts.json             -> tts_daemon.py читает ОДИН раз при старте
  ru_speaker, en_speaker, pitch, ru_model

yumi/tts.py                          -> клиент демона, профиль, кэш WAV, перезапуск
yumi/ears.py                         -> клиент STT-демона (wake/listen/mute/tap)
yumi/stt_daemon.py                   -> vosk + whisper + nemotron + oww_ear.py
yumi/oww_ear.py                      -> отдельный процесс openWakeWord
yumi/engines.py                      -> дешёвое наличие движков и списки голосов
```

Важные ограничения:

- `wake_ear` уже применяется на лету: следующий `ears.wake()` получает новое
  значение из `settings`. Фоновый цикл `SensesMixin.wake_set` держит микрофон
  120-секундными окнами, поэтому **любая проверка из панели должна сначала
  поставить цикл на паузу**, иначе уши ответят «занято».
- `stt_engine` уже применяется на лету для `listen` и для чтения фразы после
  имени. Nemotron опционален: если модели нет, `transcribe_said` откатывается
  на whisper/vosk.
- `tts_daemon.py` читает `tts.json` только при запуске: смена голоса требует
  перезапуска демона. `yumi/tts.py::close()` ставит `_shutting_down=True`
  навсегда — для перезапуска нужен отдельный путь, сбрасывающий флаг.
- Профиль TTS в `tts.json` остаётся совместимым: при старте Юми значения из
  файла подтягиваются в `settings` (ручная правка файла не теряется), а при
  изменении из панели/GTK пишутся в оба места.

---

## 4. Разбивка на подзадачи (для выполнения по одной)

| # | Подзадача | Файлы | Готово, когда |
|---|-----------|-------|----------------|
| 1 | Ключи TTS в настройках | `yumi/config.py` | 4 ключа в `DEFAULT_SETTINGS`, подписи ru/en, `tts.json` подтягивается при загрузке |
| 2 | Каталог панели | `yumi/webpanel/catalog.py` | ключи в `KEY_SECTION`, `CHOICES`, `TYPES`, `RANGES`, `TITLES`, `HINTS`; схема их отдаёт |
| 3 | Профиль TTS | `yumi/tts.py` | `PROFILE_KEYS`, `profile_from_settings`, `current_profile`, `sync_profile`, `restart` без вечного `_shutting_down` |
| 4 | Движки | `yumi/engines.py` | `status(settings)` без импорта GTK/venv; пути oww/vosk/whisper/nemotron/tts |
| 5 | Мост | `yumi/ui/bridge.py`, `yumi/ui/senses.py` | `voice_engines`, `voice_test_wake`, `voice_test`, `voice_listen(engine=)`, `wake_pause/resume` |
| 6 | HTTP | `yumi/webpanel/server.py` | 3 новых роута, `engine` в `/api/voice/listen`, аудит вызовов |
| 7 | Панель | `yumi/webui/static/js/pages/voice.js` | три карточки, статусы, кнопки проверки, ошибки в тостах |
| 8 | GTK | `yumi/brain.py`, `yumi/ui/app.py` | строки TTS, циклы, живое применение профиля |
| 9 | Снимок | `yumi/ui/webpanel.py` | `runtime.voice` (или `voice_engines`) в `/api/status` |
| 10 | Тесты | `tests/test_webpanel.py`, `tests/test_tts.py` | новые ключи в схеме, роуты отвечают, профиль пишется/сравнивается |
| 11 | Документация | `README.md`, `README.ru.md`, этот файл | описано, где выбрать и как проверить |
| 12 | Живая приёмка | — | имя/фраза/голос проверены на микрофоне и колонках |

Подзадачи 1–11 закрыты в этом коммите; открыта только 12 (живая приёмка).

---

## 5. Ключевые файлы

| Файл | Роль |
|------|------|
| `yumi/config.py` | `DEFAULT_SETTINGS`, подписи GTK, загрузка `tts.json` |
| `yumi/webpanel/catalog.py` | карта настроек панели (без GTK) |
| `yumi/tts.py` | клиент Silero-демона, профиль, перезапуск, кэш |
| `yumi/tts_daemon.py` | сам Silero-демон; читает `tts.json` при старте |
| `yumi/ears.py` | клиент STT-демона |
| `yumi/stt_daemon.py` | vosk + whisper + nemotron + oww; `TUNE`, пороги |
| `yumi/oww_ear.py` | openWakeWord в отдельном процессе |
| `yumi/engines.py` | новый: наличие движков и список голосов |
| `yumi/ui/bridge.py` | контракт HTTP ↔ приложение |
| `yumi/ui/senses.py` | фоновый wake-цикл, `speak`, пауза/возобновление |
| `yumi/ui/webpanel.py` | снимок `/api/status`, применение настроек |
| `yumi/webpanel/server.py` | роуты `/api/voice/*` |
| `yumi/webui/static/js/pages/voice.js` | страница «Ушки и голос» |
| `tests/test_webpanel.py` | схема + HTTP-роуты |
| `tests/test_tts.py` | клиент TTS и профиль |

---

## 6. Проверки

```bash
for f in tests/test_*.py; do
  python3 "$f" || echo "FAIL $f"
done
python3 -m py_compile yumi/config.py yumi/tts.py yumi/engines.py \
  yumi/ui/bridge.py yumi/ui/senses.py yumi/ui/app.py yumi/ui/webpanel.py \
  yumi/webpanel/*.py
for f in yumi/webui/static/js/*.js yumi/webui/static/js/pages/*.js; do
  node --input-type=module --check < "$f" || echo "FAIL $f"
done
```

`pytest tests/ -q` не подходит: старые standalone-тесты вызывают `sys.exit(0)` и
ломают сбор. Единственное известное падение — `tests/test_wake.py` при нестандартном
`phrase_tail_ms` в локальном `~/.config/yumi/settings.json` (см. раздел 2).

Живая проверка (после запуска Юми):

1. Открыть панель → «Ушки и голос».
2. «Активация»: выбрать `openWakeWord`, нажать «Проверить имя», сказать
   «Юми». Ожидание: «услышала: Юми». Повторить с `vosk`.
3. «Судья»: выбрать `whisper`, нажать «Проверить судью», сказать фразу.
   Повторить с `nemotron` (если модель установлена).
4. «Голос»: выбрать русский голос и высоту, нажать «Послушать голос».
   Голос должен звучать без перезапуска Юми.
5. GTK: «Настройки → Звук» показывает те же TTS-строки.

---

## 7. Известные риски

- **Микрофон один.** Проверка из панели обязана ставить фоновый wake-цикл на
  паузу; иначе `ears.wake`/`ears.listen` вернут «занято». Пауза/возобновление
  должны быть в GTK-потоке через `App._web_call`.
- **Долгий первый запуск.** Первый `tts.speak` может скачивать голоса и
  стартовать демон до 20 с; HTTP-запрос из панели должен это пережить, в
  интерфейсе — честное «готовлю голос…».
- **Ручной `tts.json`.** Пользователь мог править файл вручную; при загрузке
  Юми значения из файла подтягиваются в настройки, чтобы не перезаписать их
  дефолтами.
- **Nemotron отсутствует.** Выбор `nemotron` без модели не должен ломать
  судью: `stt_daemon.transcribe_said` уже откатывается на whisper/vosk. В
  панели это показывается как «нет модели, будет запасное ухо».
- **Смена голоса = перезапуск демона.** Не дёргать перезапуск на каждое
  сохранение настроек: `sync_profile` сравнивает профиль и перезапускает
  только при реальном изменении.
