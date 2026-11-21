# Юми: передача после обновления NLU

Документ для агента или разработчика, который будет продолжать работу над голосовыми
командами. Все три этапа плана (`~/.opencode/plan/yumi-nlu-tasks.md`) **реализованы**;
изменения пока не закоммичены.

## Что добавлено и что не добавлялось

В Юми уже были rules-движок фраз, ONNX-классификатор, разбор имени, уши и TTS. Добавлено
только недостающее:

- структурированный результат распознавания (`IntentResult`) и общие сущности фразы;
- декларативные поля кнопки в `actions.json` (`id`, `priority`,
  `requires_confirmation`, `entities`);
- порог уверенности `nlu_confidence` и альтернативы.

**Не портировались** Rust-крейты, Event Bus, YAML-конфиг, отдельный LLM-fallback и
аудио-слой — у Юми для этого есть свои Python-эквиваленты.

---

## 1. Контракт распознавания

`yumi/nlu.py`:

- `IntentResult` (dataclass): `action`, `confidence`, `slots`, `entities`,
  `alternatives`, `intent`. Метод `as_tuple()` возвращает старый тройной контракт
  `(action, score, slots)`.
- `recognize(text, actions, use_intents=True, min_confidence=None)` — новая точка входа.
  Держит прежний порядок оценок: целая фраза `1.00` > keyword `0.80` > «огрех ушек»
  `0.60`; при равенстве побеждает первая кнопка в списке. При `priority` он
  разрешает **только** равенство оценок, никогда не перебивает более высокий score.
  Собирает до трёх `alternatives` в виде `(action, score)`, отсортированных по убыванию.
- `match(text, actions, use_intents=True, min_confidence=None)` — тонкая обёртка над
  `recognize()` для обратной совместимости. Старые вызывающие (`brain`, тесты) не
  менялись.

`intent` — `id` кнопки, иначе `custom:<command>` (для встроенных команд — их id в
других модулях, здесь пока только контракт).

`yumi/entities.py` — единственный дом для сущностей, не импортирует `brain`, `chat`
или GTK:

- `spoken_number`, `bare_volume`, `VOLUME_*` — перенесены из `brain.py`; там остались
  алиасы, чтобы `music.py` и `tests/test_music_text.py` работали как раньше;
- `volume()` — `'максимум' → 100`, `'половина' → 50`, `'громкости 30' → 30`,
  `'тише' → -10` с флагом `relative`;
- `time_of_day()` — `'утром' → 08:00`, `'вечером' → 18:00`, `'полдень' → 12:00`;
- `relative_date()` — `сегодня/завтра/послезавтра/вчера` и их английские аналоги,
  плюс готовый ISO-день в поле `date`;
- `extract(text, lang='ru')` — словарь `{ключ: {'value':…, 'raw':…}}`.

Форма значения сущности — `{'value': ..., 'raw': ...}` (у даты ещё `offset` и `date`,
у шага громкости `relative: True`).

---

## 2. Кнопки: `actions.json`

`config.load_actions()` — whitelist. Старые записи без новых полей получают значения
по умолчанию, миграция `settings.json`/`actions.json` не нужна.

| поле | тип | смысл |
|------|-----|-------|
| `id` | строка ≤ 64 | устойчивое имя для журнала и `/api/status` → `runtime.nlu.last_intent` |
| `priority` | int −100..100 | только разрешение равенства оценок |
| `requires_confirmation` | bool | Юми переспрашивает перед запуском |
| `entities` | dict/list из `volume`, `time`, `date` | какие значения достать из фразы |

`phrases`, `keywords`, `speak`, `url`, `command`, `label`, `label_ru`, `icon` — как раньше.
`speak` остаётся единственным полем-ответом; второго `responses` нет намеренно.

**Подстановки.** В `speak` и `url` заменяются `{query}` и `{<entity>}`:
`{volume}` → `50`, `{time}` → `08:00`, `{date}` → ISO-дата. Для URL значение кодируется
через `urllib.parse.quote`. **Команда (`command`) из услышанных слов никогда не
собирается** — это граница безопасности, не менять её без отдельного решения.

`yumi/ui/webpanel.py:_clean_actions()` — тот же whitelist для записи из браузера.
Любое новое поле добавляется **в оба места** (`config.load_actions` и `_clean_actions`),
иначе кнопка потеряет его при сохранении из панели.

---

## 3. Подтверждение

Состояние живёт в `Brain` рядом с `note_pending`:

- `intent_confirm` — `None` или `{'action':…, 'slots':…, 'text':…}`;
- `last_intent`, `last_confidence` — для панели и журнала.

Поток:

1. `voice_intent()` видит `requires_confirmation`, запоминает команду, говорит
   `confirm_ask`, ставит `listening=True` и шлёт запрос `('listen', 15)`.
2. `heard_text()` проверяет `intent_confirm` **до** остальных ветвей: `да/yes` →
   `_voice_run()`, `нет/no` → `confirm_no`, любая другая фраза сбрасывает ожидание и
   разбирается заново. `stop_command` и `stop_all()` имеют приоритет и тоже снимают
   ожидание.
3. Панель: `WebBridge.confirm_action(ok)` → `POST /api/nlu/confirm` с
   `{"ok": true|false}` → тот же `_voice_confirm_web()`, что и голосовой ответ;
   публикует SSE-событие `actions`.

Новые строки интерфейса (RU/EN) в `config.py`: `confirm_ask`, `confirm_yes`,
`confirm_no`, `ring_id`, `ring_priority`, `ring_confirm`, `ring_phrases`,
`ring_keywords`, `ring_speak`, `ring_url`.

`/api/status` → `runtime.nlu = {last_intent, last_confidence, confirm}`. Страница
«Кнопки» показывает карточку подтверждения по `confirm` и подписывается на
`ctx.onStatus`.

---

## 4. Где редактируются поля (паритет GTK / веб)

- **GTK** (`yumi/extras.py`): форма кнопки стала четырёхстраничной (`FORM_PAGES`):
  1) команда/имя, 2) фразы/keywords, 3) `speak`/`url`, 4) `id`/`priority` и
  переключатель «Спрашивать». `form_page` — **относительный** сдвиг (`-1`/`+1`),
  `form_confirm` переключает флаг, `Tab` ходит только по полям текущей страницы,
  `Enter` сохраняет, `Escape` отменяет. `ring_add(..., voice=f)` переносит все поля
  в `actions.json`.
- **Веб** (`yumi/webui/static/js/pages/buttons.js`): те же поля в редакторе, плюс
  карточка подтверждения и отображение `id/priority/entities` на карточке кнопки.
  Только `createElement`/`textContent`, строки RU/EN через `lang()`.

Если добавляете новое поле кнопки: `config.load_actions` → `_clean_actions` →
`FORM_PAGES` + `form_open` + `ring_add` → `buttons.js` (редактор и карточка) →
тесты → обе README.

---

## 5. Порог уверенности

`nlu_confidence` в `DEFAULT_SETTINGS` = `0.6`. В `webpanel/catalog.py`: секция `mind`,
тип `range`, `RANGES = (0.5, 1.0, 0.05)`, RU/EN заголовок и подсказка. Читается
`brain.voice_intent()` **на каждой фразе**, поэтому PATCH `/api/settings` из панели
применяется сразу; при выключении `nlu` сбрасывается отложенное подтверждение.
Точные фразы (`1.00`) проходят порог `1.0`, keyword (`0.80`) и fuzzy (`0.60`) — нет.
Порог `intents.py` (`MIN_PROB = 0.85`) не трогался; к ONNX-результату новый порог
тоже применяется.

---

## 6. Тесты

Все тесты в репозитории — отдельные assert-скрипты; `pytest tests/ -q` **ломается** на
`tests/test_arbiter.py`, который зовёт `sys.exit(0)` при импорте (это старое поведение,
не следствие обновления). Надёжный способ прогнать всё:

```bash
for f in tests/test_*.py; do python3 "$f" || echo "FAIL $f"; done
python3 -m py_compile yumi/webpanel/*.py yumi/config.py yumi/brain.py \
  yumi/extras.py yumi/nlu.py yumi/entities.py yumi/ui/*.py
for f in yumi/webui/static/js/*.js yumi/webui/static/js/pages/*.js; do
  node --input-type=module --check < "$f" || echo "FAIL $f"
done
```

Новые и расширенные тесты:

- `tests/test_entities.py` — парсеры сущностей;
- `tests/test_nlu.py` — `IntentResult`, `alternatives`, priority, `min_confidence`,
  обратная совместимость `match()`;
- `tests/test_voice_intent.py` — end-to-end: сущности в `speak`, `{query}` в `url`,
  порог, подтверждение голосом и из панели, «стоп»;
- `tests/test_ring_editor.py` — страницы GTK-формы и перенос полей в действий;
- `tests/test_webpanel.py` — схема `nlu_confidence`, `_clean_actions`, PUT/GET
  `/api/actions` с новыми полями, `POST /api/nlu/confirm` + CSRF.

---

## 7. Подводные камни

1. Ложные срабатывания: keyword даёт `0.80`, fuzzy `0.60`. Не снижать порог
   бездумно; не трогать `NEGATERS` (`не открывай…`) и `SEARCH_VERB`
   («что происходит в интернете» — не поиск).
2. `{query}-URL` без предмета не срабатывает; keyword-поиск требует глагола поиска.
   При добавлении новых сущностей не ломайте эту защиту.
3. ONNX-fallback привязан к `label` кнопок. Появление `id` не должно ломать
   `labels.json` (новые кнопки просто не попадают в offered).
4. `yumi/webpanel/*` не импортирует GTK — только `WebBridge` и `GLib.idle_add`.
5. `entities.py` не должен импортировать `brain`/`chat`/GTK (циклы).
6. `CONFIRM_YES/NO` объявлены внизу `brain.py`, но используются из методов класса —
   это безопасно, пока модуль загружен целиком; при выносе в отдельный файл импорт
   не должен стать циклическим.
7. Секреты и токен панели по-прежнему не отдаются в открытом виде; новый роут
   `/api/nlu/confirm` — мутация, поэтому требует CSRF.

## 8. Что осталось (идеи, не часть сделанного)

- Показывать `alternatives` в панели (сейчас только в `IntentResult` и журнале).
- Общую таблицу синонимов («интернет/браузер/бразер») переиспользовать в
  `addressing.py`, сохранив веса `0.6/0.8` и не давая `1.0`.
- При желании — отдельный экран доверия к командам (журнал «кто просил / что
  запустилось») поверх уже существующего `web-audit.log`.
