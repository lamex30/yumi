# Handoff: Wake Word Engine «Юми» на openWakeWord (yumi-wake-engineb)

> Документ передачи работы. Обновляется **в одном коммите с кодом** — любой
> агент, взявший работу с этого места, продолжает с раздела «Статус» и
> «Как подхватить», не переоткрывая сделанное. Этот документ описывает
> выполнение ТЗ из `yumi-wake-engineb.md` (в корне репозитория).

---

## 0. Как подхватить работу

1. `git branch --show-current` → должно быть `yumi-wake-engineb`; `git status` → чисто.
2. Прочитать раздел «Статус»: ✅ — сделано и закоммичено, 🔜 — осталось.
3. Прогнать тесты (раздел 8) — все зелёные, это точка отсчёта.
4. Брать работу **только из раздела «Осталось сделать»**, сверху вниз.
5. Каждое изменение: код + правка этого документа + один коммит.

---

## 1. Задача (кратко)

OpenWakeWord с моделью `yumi.onnx` — единственный детектор имени «Юми».
Vosk/Nemotron/Whisper — только распознавание команды ПОСЛЕ пробуждения.
VAD оптимизирует нагрузку, но не блокирует обнаружение. STOP работает
независимо в любом состоянии. Полное ТЗ — `yumi-wake-engineb.md`.

---

## 2. Статус

### ✅ Сделано (в репозитории, ветка `yumi-wake-engineb`)

| Что | Где | Коммит |
|-----|-----|--------|
| oww-процесс: stdin-кадры → score, протокол `D+len+data` / `R`, буферизация остатка кадра | `yumi/oww_ear.py` | 5ea1779 |
| oww — единственный детектор имени в SLEEP (без Vosk/Nemotron/Whisper/судьи/фонетики) | `yumi/stt_daemon.py` → `Ears.wake()` | 5ea1779 |
| Конфиг: `wake_ear: auto/oww/vosk` в `yumi/config.py` + UI-подписи | `yumi/config.py` | 5ea1779 |
| Команда после имени не глотается: `ears.listen()` после пустого tail | `moth_girl.py` → `wake_set().work` | ec7d312 |
| Исправления oom/сирот: ear рождается внутри замка после busy-чек; `judge_note`/`Diag` восстановлены | `yumi/stt_daemon.py` | b8cc346 |
| **Канал STOP** (раздел 9): `wake()` возвращает `{'stop': True}` — лёгкий независимый стоп-канал на vosk small (без LLM), работает в SLEEP (стоп-слово в проходящей речи, как раньше), LISTENING (только фраза-«стоп» целиком: «останови музыку» идёт в STT командой), SPEAKING (через mute — только фраза-стоп целиком, `stop_shout`) | `yumi/stt_daemon.py` → `Ears.wake()` | **16c8108** |
| **Хвост фразы из кольцевого буфера** (раздел 7): ring копится каждым чанком; при пробуждении снимается лид `wake_lead_ms` (1 с — OWW отвечает на 1–2 кадра ВНУТРИ имени), фраза дослушивается VAD-endpointing (400 мс речи + `VAD_TAIL` 1.2 с; 1.5 с тишины = имя без команды) и уходит в STT (`transcribe_said`: Nemotron/Whisper по `stt_engine`) прямо в `wake()`; `ears.listen()` в moth_girl остаётся фолбэком («имя прозвучало, команда позже») | `yumi/stt_daemon.py` | **16c8108** |
| **Mute в wake** (раздел 8): `muted()` проверяется — во время TTS ухо/ring/лента не видят её голос; после mute всё сбрасывается (ухо `'R'`, кольцо, детектор, стоп-ухо); речь сразу после TTS не теряется; детектор теперь один на окно (не на каждый чанк) | `yumi/stt_daemon.py` | **16c8108** |
| **vosk-фолбэк** (раздел 16): `wake_ear: vosk` снова работает — имя из vosk-слов (`wake_at`, budget=1); `auto` без модели больше не глухой | `yumi/stt_daemon.py` | **16c8108** |
| Гистерезис: настоящий `EarStat` (hold-гистерезис, `oww_window` из конфига) вместо inline-копии; решение вынесено в чистую `oww_wake_decision()` | `yumi/stt_daemon.py` | **16c8108** |
| Cooldown (раздел 6): `oww_cooldown` (2 с, 0–30) — эхо отработанного имени не будит второй раз | `yumi/stt_daemon.py` | **16c8108** |
| Диагностика (раздел 11): `wake.jsonl` несёт `state/vad_active/wake_score/wake_threshold/wake_latency_ms/buffer_duration_ms/tts_active/aec_active`; события `CANDIDATE/STT/STOP/TIMEOUT`; latency от первого голоса на ленте (раньше всегда ≈0) | `yumi/stt_daemon.py` | **16c8108** |
| Тесты: `test_oww.py` — fix `sys.path` (модельный тест не скипает под oww-venv), `oww_wake_decision` (спайк не будит, растяжка будит, hold держит), Utterance-лид, канал стопа/mute/фолбэк по исходнику | `tests/test_oww.py` | **16c8108** |
| **Артефакт отката**: `yumi/stt_daemon.py.v27-judge.bak` — последний демон со старым движком (vosk+судья+nemo-стоп), извлечён из `035e6a9` | корень | **8594715** |
| Метрики/сравнение: `tools/wake_metrics.py` (wake.jsonl), `tools/compare_wake.py` (fix f-string) | `tools/` | 8594715 |

### 🔜 Осталось сделать (в порядке приоритета)

1. **Живая проверка** (нужен микрофон/колонки, вживую):
   - «Юми, включи музыку» одним дыханием → `wake.jsonl` должен показать
     `WAKE … phrase_after_name` с текстом команды, `moth_girl` передаёт её в brain.
   - «Юми» без команды → 1.5 с тишины → `name_alone` → она спрашивает;
     затем команда в окно `ears.listen()` — не теряется.
   - «СТОП!» во время TTS → `STOP`-событие, TTS обрывается (`b.stop_all`).
   - TTS со своим именем («Меня зовут Юми») → не просыпается (mute).
   - Длительная тишина 10+ мин → fed=read в `judge.log` SUMMARY, RAM не растёт
     (ear умирает/рождается каждые 120 с — это норма, проверять по `ps`).
2. **Калибровка порогов на записях** (раздел 6 ТЗ): `tools/compare_wake.py`
   по `dataset/` и `/tmp/yumi`-кольцу; при пропусках — опустить
   `oww_threshold` (v5-модель: живые клипы 0.43–0.82, см. handoff-oww);
   при ложных на музыке — поднять `oww_threshold`/`oww_spike`.
3. **Сравнение старого и нового движка** на одних записях (раздел 14 ТЗ):
   `python3 tools/compare_wake.py --dataset` → `wake_metrics.py`; записать
   recall/FAR в итоговый отчёт. Внимание: «старый движок» в compare_wake —
   упрощённый vosk-токен на WAV, не полный конвейер; для честного сравнения
   старого конвейера — прогнать `yumi/stt_daemon.py.v27-judge.bak`.
4. **CPU/RAM замеры** (раздел 13 ТЗ): idle (SLEEP) с oww-ухом и стоп-каналом:
   `top -b -n1` по pid демона + ear-процесса; потерянные кадры —
   `SUMMARY fed= read=` в `/tmp/yumi/judge.log` (lag логируется раз в 10 с).
5. **Итоговый отчёт** (раздел 18 ТЗ): схема старой/новой архитектуры
   (раздел 3 ниже готова), списки файлов, результаты тестов, CPU/RAM,
   задержка, ложные/пропущенные, ограничения модели, инструкция запуска
   и отката — всё уже есть в этом документе, собрать в финальный ответ.
6. Опционально: перечитать `yumi-wake-engineb.md` §14 (список из 15
   тест-сценариев) и отметить в отчёте, какие покрыты живой проверкой,
   какие — только synthetic (`compare_wake`), какие не покрыты.

---

## 3. Архитектура (текущая, реализованная)

```
МИКРОФОН → PipeWire/ALSA → pw-record/parecord/arecord (16 kHz mono s16)
  → AEC (module-echo-cancel, источник yumi_ec; её голос вычитается)
  → HPF (однополюсный high-pass: гул/рокот)
  → кольцевой буфер ring (~8 с чистого звука, копится КАЖДЫМ чанком)
      |
      +-- SLEEP: Detector (webrtcvad, ОДИН на окно, состояние живёт)
      |     → OpenWakeWord yumi.onnx (отдельный процесс oww_ear.py,
      |         кадры 80 мс / 2560 байт, score за кадр)
      |         → EarStat (растяжка ≥ oww_spike кадров ≥ oww_threshold,
      |             hold-гистерезис 0.35) → oww_wake_decision
      |             → cooldown oww_cooldown → WAKE EVENT
      |
      +-- STOP-канал (независимый, лёгкий): vosk small на
      |     очищенном звуке; SLEEP — stop_at/stop_mush на partials,
      |     LISTENING — только stop_shout (фраза-«стоп» целиком),
      |     SPEAKING (mute) — stop_shout по finals
      |
      +-- muted() (TTS играет): ear/ring/лента НЕ видят её голос,
            стоп-канал продолжает; после mute — полный сброс

WAKE EVENT → LISTENING (в том же wake-окне):
  - лид фразы = последние wake_lead_ms кольца (голова имени не теряется)
  - фраза копится в Utterance; конец по VAD endpointing:
    400 мс речи + VAD_TAIL (1.2 с) тишины; 1.5 с тишины без речи = имя без
    команды → {'woke': True, 'text': ''} → moth_girl зовёт ears.listen()
  - стоп-канал продолжает работать
  - фраза → transcribe_said (Nemotron/Whisper по stt_engine)
    → {'woke': True, 'text': ...} → moth_girl → brain.wake_up(text)

SPEAKING: TTS → self_mute → /mute (демон); wake-окно продолжает крутиться,
  но ear/ring глухи к её голосу; «СТОП» → STOP-канал → b.stop_all().

Отдельный путь: ear-процесс умирает/рождается каждые 120 с (secs=120 из
moth_girl) — это норма, ~190 МБ RAM на окно, рождается внутри замка.
```

Состояния SLEEP/LISTENING/SPEAKING живут внутри `wake()` + `moth_girl`
(отдельного state-машины в демоне нет — `state` в диагностике отражает фазу).

---

## 4. Ключевые файлы

| Файл | Роль |
|------|------|
| `yumi/stt_daemon.py` | Демон ушей: HTTP API `/wake /listen /tap /mute /stop /mics /ping`; вся логика wake() |
| `yumi/oww_ear.py` | Отдельный процесс openWakeWord (stdin/stdout) |
| `yumi/ears.py` | Клиент: запуск демона, `/wake` (передаёт `oww_ear`), `/listen`, `/mute` |
| `yumi/config.py` | `wake_ear: auto/oww/vosk`, `wake_ears`, `wake_word`, `stt_engine` и пр. |
| `moth_girl.py` | Wake-цикл приложения: `ears.wake()` каждые 0.15 с, `res.get('stop')` → `b.stop_all()`, текст → `b.wake_up(t)`, пусто → `ears.listen()` |
| `yumi/brain.py` | `heard_text`: drop_name, `stop_command` («стоп» в команде), NLU |
| `tests/test_oww.py` | Тесты новой архитектуры (см. раздел 8) |
| `tests/test_wake.py`, `tests/test_arbiter.py`, `tests/test_stop_wake.py` | Регресс старых компонентов (EarStat, стоп-слова) — зелёные |
| `tools/oww_probe.py` | Пробник модели (микрофон/WAV) |
| `tools/compare_wake.py`, `tools/wake_metrics.py` | Сравнение движков и метрики wake.jsonl |
| `yumi/stt_daemon.py.v27-judge.bak` | Артефакт отката: старый движок (vosk+судья) целиком |
| `docs/handoff-oww.md` | Устаревший handoff (vosk+судья) — только для контекста |

---

## 5. Конфигурация (`~/.config/yumi/settings.json`, раздел 12 ТЗ)

| Ключ | По умолчанию | Что делает |
|------|--------------|-----------|
| `wake_ear` | `auto` | `auto` (обученное ухо, когда есть) / `oww` (только оно) / `vosk` (старое ухо без судьи) |
| `oww_threshold` | `0.5` | линия активации растяжки |
| `oww_sure` | `0.7` | «уверенная» линия (решение сразу) |
| `oww_hold` | `0.35` | hold-гистерезис: ниже — имя кончилось |
| `oww_window` | `3.0` | окно памяти EarStat, с |
| `oww_spike` | `2` | кадров подряд над порогом = вера (одиночный спайк не будит) |
| `oww_cooldown` | `2.0` | с тишины после wake до следующего разрешения (0–30) |
| `wake_lead_ms` | `1000.0` | мс кольца перед точкой пробуждения → лид фразы (0–4000) |
| `wake_listen` | `15.0` | с максимум на дослушивание фразы после имени (5–30) |
| `phrase_tail_ms` | `1200` | тишина конца фразы (VAD_TAIL) |
| `wake_after` | `1.5` | с тишины после имени без команды |
| `oww_stream_raw` | `true` | ухо ест звук до noise gate (тихие буквы целы) |
| `stt_diag_wav` | `false` | диагностический WAV (`/tmp/yumi/wake_feed.wav`) |
| `wake_judge_log` | `true` | дневник `/tmp/yumi/judge.log` + события `wake.jsonl` |

Диагностика: `/tmp/yumi/wake.jsonl` (машина-читалка: `tools/wake_metrics.py`),
`/tmp/yumi/judge.log` (EAR/WAKE/STOP/SUMMARY fed=read), WAV — `stt_diag_wav`.

---

## 6. Откат (раздел 16 ТЗ) — теперь реальный

Вариант A (лёгкий, без git): `settings.json` → `"wake_ear": "vosk"` — имя
ловит vosk (без судьи: точное и одно-буквенное «юми», ложных на музыке
больше). Демон/приложение перезапустить не нужно (TUNE читается на старте
окна — но демон держит TUNE в памяти: перезапустить приложение `bin/yumi --quit` → `bin/yumi`).

Вариант B (полный старый движок):
```bash
cp yumi/stt_daemon.py.v27-judge.bak yumi/stt_daemon.py   # VERSION 27 совпадает
bin/yumi --quit && bin/yumi                              # клиент убьёт/перезапустит демона
python3 tests/test_wake.py                               # vosk-путь зелёный
```
`/wake` старого демона игнорирует новый параметр `oww_ear` — клиент совместим.
Ветка `yumi-wake-engineb` при откате не трогается, `main` не трогается.

---

## 7. Известные риски и ограничения

- **Ложные срабатывания на музыке/видео** — главный риск oww. Пороги
  калибровать по записям (раздел «Осталось», п. 2); судьи больше нет.
- **vosk-фолбэк без судьи** — на музыке будет чаще ошибаться в «юми»
  (`wake_at` budget=1). Это осознанный компромисс режима отката.
- **SLEEP-стоп по проходящей речи** (`stop_at` на partials) — паритет со
  старым движком: фраза собеседника со словом «останови» может остановить её.
  В LISTENING уже защищено (только фраза-стоп целиком).
- **Cooldown 2 с** блокирует повторное «юми» в первые 2 с после wake —
  выключается `oww_cooldown: 0`.
- **Клиппинг микрофона глушит модель** (тот же клип: колонки 0.17–0.36,
  чистый 0.94). Усиление уже сбавлено (Boost 1 / Capture 38 / Digital 85).
- ear-процесс пересоздаётся каждые 120 с (модель грузится заново) —
  латентность первых кадров окна чуть выше; CPU/RAM — замерить (п. 4).
- Модель v5 (`oww/models/yumi.onnx`): живые клипы 0.43–0.82 при пороге 0.4;
  синтетика TTS просела до 58% @0.5 — рантайму не важно (голос живой).

---

## 8. Команды

```bash
# тесты
python3 tests/test_wake.py        # старые компоненты (vosk, стоп-слова)
python3 tests/test_arbiter.py     # EarStat, фонетика
python3 tests/test_stop_wake.py   # стоп доходит до brain
~/.local/share/yumi/oww/bin/python tests/test_oww.py   # новая архитектура
                                                        # (модельный тест требует oww-venv;
                                                        #  системный python скипнет его честно)

# диагностика
python3 tools/wake_metrics.py /tmp/yumi/wake.jsonl
tail -f /tmp/yumi/judge.log

# сравнение на записях
python3 tools/compare_wake.py --dataset
python3 tools/wake_metrics.py /tmp/yumi/wake.jsonl

# пробник модели
./tools/oww_probe.py --models yumi          # микрофон
./tools/oww_probe.py --models yumi --wav f.wav

# приложение
bin/yumi --quit && bin/yumi

# git
git branch --show-current     # yumi-wake-engineb
git status                    # чисто перед изменениями
# НЕ делать git reset --hard; main не трогать
```

Пути: модель `YUMI_OWW_MODEL` или `~/.local/share/yumi/oww/models/yumi.onnx`;
oww-venv `~/.local/share/yumi/oww` (python 3.11, openwakeword 0.6.0);
stt-venv `~/.local/share/yumi/stt` (vosk + faster-whisper).

---

## 9. История коммитов (этот handoff)

| Коммит | Суть |
|--------|------|
| `5ea1779` | oww-only движок (первая версия) |
| `103e082`, `6cddce2` | handoff |
| `b8cc346` | oom/сироты/judge_note/Diag |
| `ec7d312` | listen() после пустого tail + тесты + compare_wake |
| `0262466` | модель v5 в прод |
| `16c8108` | **канал STOP, хвост фразы из кольца, mute, vosk-фолбэк, cooldown, diag, тесты** |
| `8594715` | **артефакт отката v27-judge.bak + fix compare_wake** |
| *(следующий)* | живая проверка / калибровка / сравнение / итоговый отчёт |

---

## 10. Главный принцип

Не усложнять систему без необходимости. Одна модель OpenWakeWord отвечает за
обнаружение имени. VAD помогает экономить ресурсы, но не блокирует обращения.
STT запускается после пробуждения. STOP работает независимо от состояния.
Сначала изучить проект (этот документ + ТЗ), затем править, затем испытывать.
