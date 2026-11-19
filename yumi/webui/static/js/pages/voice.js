import { api } from '../api.js';
import {
  el, tr, errToast, toast, sectionTitle, buildSetting, emptyBox, badge,
} from '../ui.js';

const CARD_KEYS = {
  activation: ['wake_ears', 'wake_word', 'wake_ear', 'wake_sensitivity',
    'wake_confirm', 'wake_model_size'],
  judge: ['stt_engine', 'stt_size', 'phrase_tail_ms', 'wake_after', 'wake_judge_log'],
  voice: ['voice_kind', 'voice_vol', 'tts_ru_speaker', 'tts_en_speaker',
    'tts_pitch', 'tts_ru_model'],
  rest: ['stt_mic', 'stt_echo', 'stt_selfmute', 'name_duck', 'tap_ears', 'timer_sound'],
};
const ALL_KEYS = [...new Set(Object.values(CARD_KEYS).flat())];

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let status = ctx.status || {};
  let drawnSig = '';

  const say = (ru, en) => (lang() === 'ru' ? ru : en);
  const entries = () => {
    const map = {};
    for (const entry of (ctx.schema?.settings || [])) map[entry.key] = entry;
    return map;
  };

  view.append(
    el('h2', { class: 'pagehead', text: say('Ушки и голос', 'Ears & voice') }),
    el('p', { class: 'pagelead', text: say(
      'Какое ухо ловит имя, кто читает фразу и каким голосом она отвечает — всё можно проверить здесь же.',
      'Which ear hears the name, who reads the phrase and which voice answers — test all three right here.') }),
  );

  // ---------------------------------------------------------------- live state
  const heard = el('div', { class: 'bubble', text: say('Нажми «Слушать» и скажи что-нибудь.', 'Press “Listen” and say something.') });
  const secs = el('input', { type: 'number', min: 2, max: 30, value: 8, style: { maxWidth: '80px' } });
  const speakInput = el('input', { type: 'text', placeholder: say('Что ей сказать?', 'What should she say?') });
  const testCard = el('div', { class: 'card' },
    el('h3', { text: say('Быстрая проверка слуха', 'A quick listening test') }),
    el('div', { class: 'row' },
      secs,
      el('button', {
        class: 'btn btn-primary', type: 'button', text: say('Слушать', 'Listen'),
        onclick: async () => {
          heard.textContent = say('слушаю…', 'listening…');
          try {
            const data = await api.post('/api/voice/listen', { seconds: Number(secs.value) || 8 });
            heard.textContent = data.heard
              ? `${say('услышала', 'heard')}: «${data.heard}» (${data.engine || ''})`
              : say('тишина', 'silence');
          } catch (err) {
            heard.textContent = err.message;
            errToast(err);
          }
        },
      }),
      el('button', {
        class: 'btn btn-danger', type: 'button', text: say('Закрыть ушки', 'Stop listening'),
        onclick: async () => {
          try {
            await api.post('/api/voice/stop');
            heard.textContent = say('ушки закрыты', 'ears closed');
          } catch (err) { errToast(err); }
        },
      })),
    el('div', { class: 'row', style: { marginTop: '10px' } },
      speakInput,
      el('button', {
        class: 'btn', type: 'button', text: say('Сказать', 'Speak'),
        onclick: async () => {
          const text = speakInput.value.trim();
          if (!text) return;
          try {
            await api.post('/api/voice/speak', { text });
            toast(say('Говорит', 'Speaking'), 'good');
          } catch (err) { errToast(err); }
        },
      })),
    el('div', { style: { marginTop: '12px' } }, heard));

  const live = el('div', { class: 'card' },
    el('h3', { text: say('Прямо сейчас', 'Right now') }));

  view.append(el('div', { class: 'grid two' }, testCard, live));

  // ---------------------------------------------------------------- the three pickers
  const activationBox = el('div', { class: 'card' });
  const judgeBox = el('div', { class: 'card' });
  const voiceBox = el('div', { class: 'card' });
  const restBox = el('div', { class: 'card', style: { marginTop: '16px' } });

  view.append(sectionTitle(say('Активация, судья и голос', 'Activation, judge and voice')));
  view.append(el('div', { class: 'stack' }, activationBox, judgeBox, voiceBox));
  view.append(sectionTitle(say('Тонкая настройка', 'Fine tuning')));
  view.append(restBox);

  function value(key) {
    return status.settings?.[key];
  }

  async function apply(key, next) {
    try {
      const data = await ctx.applySettings({ [key]: next });
      if (data?.result?.saved) {
        status.settings = { ...(status.settings || {}), ...data.result.saved };
      }
      return data;
    } catch (err) {
      errToast(err);
      return null;
    }
  }

  function fill(box, keys, title, lead) {
    box.replaceChildren(el('h3', { text: title }));
    if (lead) box.append(el('p', { class: 'card-hint', text: lead }));
    const map = entries();
    let any = false;
    for (const key of keys) {
      const entry = map[key];
      if (!entry) continue;
      any = true;
      box.append(buildSetting(entry, value(key), (next) => apply(key, next)));
    }
    if (!any) box.append(emptyBox(say('Нет настроек', 'No settings')));
    return box;
  }

  function engineLine(label, info, note) {
    const ok = Boolean(info?.available);
    return el('div', { class: 'row' },
      el('span', { class: 'dot', style: { background: ok ? 'var(--mint)' : 'var(--red)' } }),
      el('span', { text: `${label}: ${ok ? say('готово', 'ready') : say('нет', 'missing')}` }),
      note ? el('span', { class: 'card-hint', text: note }) : null);
  }

  function testRow(buttonText, onclick, extra, busy) {
    const out = el('div', { class: 'bubble', text: say('результат появится здесь', 'the result will appear here') });
    const button = el('button', { class: 'btn btn-primary', type: 'button', text: buttonText, onclick: async () => {
      button.disabled = true;
      const old = button.textContent;
      button.textContent = busy?.button || say('слушаю…', 'listening…');
      out.textContent = busy?.out || say('говорю с микрофоном, подожди…', 'holding the microphone, wait…');
      try {
        const text = await onclick();
        out.textContent = text;
      } catch (err) {
        out.textContent = err.message;
        errToast(err);
      } finally {
        button.disabled = false;
        button.textContent = old;
      }
    } });
    return el('div', { class: 'row', style: { marginTop: '10px' } }, ...(extra || []), button, out);
  }

  function drawActivation() {
    const voice = status.runtime?.voice || {};
    const act = voice.activation || {};
    const seconds = el('input', { type: 'number', min: 3, max: 30, value: 8, style: { maxWidth: '80px' } });
    fill(activationBox, CARD_KEYS.activation,
      say('1. Активация: кто слышит имя', '1. Activation: who hears the name'),
      say('openWakeWord — обученное ухо «Юми»; vosk — прежнее угадывание по словам. Если имя прозевали, vosk-фолбэк разбирает хвост: команда выполняется, вопрос уходит в мозг, чужая болтовня молчит.',
          'openWakeWord is the trained «Yumi» ear; vosk is the old word guessing. If the name was missed, the vosk fallback routes the tail: a command runs, a question goes to her mind, background talk stays silent.'));
    activationBox.append(engineLine('openWakeWord', act.oww,
      act.oww?.model_ok ? `${Math.round((act.oww.model_bytes || 0) / 1024)} KB` : ''));
    activationBox.append(engineLine('vosk', act.vosk));
    activationBox.append(testRow(
      say('Проверить имя', 'Test the name'),
      async () => {
        const ear = value('wake_ear') || 'auto';
        const data = await api.post('/api/voice/test-wake', {
          seconds: Number(seconds.value) || 8, ear,
        });
        if (data.woke) {
          return say(`услышала имя (${data.ear}, ${data.elapsed} с)`,
            `heard the name (${data.ear}, ${data.elapsed}s)`);
        }
        return say(`тишина за ${data.seconds} с (${data.ear})`,
          `silence for ${data.seconds}s (${data.ear})`);
      },
      [seconds]));
  }

  function drawJudge() {
    const voice = status.runtime?.voice || {};
    const judge = voice.judge || {};
    const seconds = el('input', { type: 'number', min: 2, max: 30, value: 8, style: { maxWidth: '80px' } });
    fill(judgeBox, CARD_KEYS.judge,
      say('2. Судья: кто читает фразу', '2. Judge: who reads the phrase'),
      say('Whisper — проверенный местный распознаватель; Nemotron 0.6b — быстрый потоковый.',
          'Whisper is the reliable local reader; Nemotron 0.6b is the fast streaming one.'));
    judgeBox.append(engineLine('Whisper', judge.whisper));
    judgeBox.append(engineLine('Nemotron 0.6b', judge.nemotron,
      judge.nemotron?.available ? '' : say('нет модели — whisper прочитает запасным', 'no model — whisper reads as the fallback')));
    judgeBox.append(testRow(
      say('Проверить судью', 'Test the judge'),
      async () => {
        const engine = value('stt_engine') || 'whisper';
        const data = await api.post('/api/voice/listen', {
          seconds: Number(seconds.value) || 8, engine,
        });
        return data.heard
          ? `${engine}: «${data.heard}»`
          : say(`${engine}: тишина или не разобрала`, `${engine}: silence or nothing read`);
      },
      [seconds]));
  }

  function drawVoice() {
    const voice = status.runtime?.voice || {};
    const info = voice.tts || {};
    const sample = el('input', {
      type: 'text',
      placeholder: say('Фраза для проверки голоса', 'A phrase to test the voice'),
      value: say('Привет! Это Юми, я говорю своим голосом.', 'Hi! This is Yumi, speaking with my real voice.'),
    });
    fill(voiceBox, CARD_KEYS.voice,
      say('3. Голос: чем она говорит', '3. Voice: how she sounds'),
      say('Голос, модель и высота применяются сразу; проверка звучит даже при типе «Пищалка».',
          'Voice, model and pitch apply at once; the test plays even when the type is “8-bit blips”.'));
    voiceBox.append(engineLine(say('настоящий голос', 'real voice'), info,
      info.profile ? `${info.profile.ru_speaker} / ${info.profile.en_speaker} · ${info.profile.pitch}` : ''));
    voiceBox.append(testRow(
      say('Послушать голос', 'Listen to the voice'),
      async () => {
        const data = await api.post('/api/voice/test', {
          text: sample.value.trim() || undefined,
          lang: value('speech_lang') || lang(),
        });
        return say(`сказала «${data.text}» (${data.profile?.ru_speaker || ''} / ${data.profile?.pitch ?? ''})`,
          `said “${data.text}” (${data.profile?.ru_speaker || ''} / ${data.profile?.pitch ?? ''})`);
      },
      [sample],
      {
        button: say('синтезирую…', 'synthesizing…'),
        out: say('готовлю голос, сейчас зазвучит… (в первый раз это может занять до минуты)',
          'preparing the voice, it will sound in a moment… (the first run can take a minute)'),
      }));
  }

  function drawRest() {
    restBox.replaceChildren();
    fill(restBox, CARD_KEYS.rest,
      say('Остальные настройки ушей', 'The rest of the ears'),
      say('Микрофон, эхо, тишина и дневник ушек.', 'Microphone, echo, quiet and the ears’ diary.'));
  }

  function drawLive() {
    const runtime = status.runtime || {};
    const mics = (runtime.mics || [])
      .map((mic) => (typeof mic === 'string' ? { name: mic, label: mic } : mic))
      .filter((mic) => mic && mic.name);
    const current = value('stt_mic');
    const unavailable = runtime.unavailable || [];
    const liveText = [];
    liveText.push(el('div', { class: 'row' },
      el('span', { class: 'dot', style: { background: runtime.wake_on ? 'var(--mint)' : 'var(--red)' } }),
      el('span', { text: runtime.wake_on
        ? say('имя слышит', 'listening for her name')
        : say('имя не слышит', 'name listening is off') })));
    if (runtime.sleeping) liveText.push(badge(say('не мешать', 'dnd'), 'amber'));
    if (runtime.listening) liveText.push(badge(say('слушает фразу', 'listening'), 'mint'));
    if (runtime.busy) liveText.push(badge(say('занята', 'busy'), 'amber'));
    if (unavailable.length) {
      liveText.push(el('div', { class: 'card-hint', text: `${say('недоступно', 'unavailable')}: ${unavailable.join(', ')}` }));
    }
    live.replaceChildren(el('h3', { text: say('Прямо сейчас', 'Right now') }));
    live.append(el('div', { class: 'stack' }, ...liveText));
    live.append(el('div', { class: 'card-hint', style: { marginTop: '10px' },
      text: say('микрофон, которым она слушает:', 'the microphone she hears with:') }));
    const list = el('div', { class: 'stack', style: { marginTop: '4px' } });
    const pick = (next) => async () => {
      await apply('stt_mic', next);
      await ctx.refreshStatus();
      drawAll(true);
    };
    list.append(el('button', {
      class: `btn btn-small${current ? '' : ' btn-primary'}`, type: 'button',
      text: `${current ? '' : '✓ '}${say('авто (рабочий стол)', 'auto (the desktop)')}`,
      title: say('пусть выбирает рабочий стол', 'leave the choice to the desktop'),
      onclick: pick(null),
    }));
    for (const mic of mics) {
      const chosen = current === mic.name;
      list.append(el('button', {
        class: `btn btn-small${chosen ? ' btn-primary' : ''}`, type: 'button',
        text: `${chosen ? '✓ ' : ''}${mic.label || mic.name}`,
        title: mic.name,
        onclick: pick(mic.name),
      }));
    }
    live.append(list);
    if (!mics.length) {
      live.append(el('div', { class: 'card-hint', style: { marginTop: '4px' },
        text: say('микрофоны пока не найдены', 'no microphones found yet') }));
    }
    const find = el('button', {
      class: 'btn btn-small', type: 'button', text: say('Найти микрофоны', 'Find microphones'),
      onclick: async () => {
        find.disabled = true;
        find.textContent = say('ищу…', 'looking…');
        await refreshMics({ quiet: false });
      },
    });
    live.append(el('div', { class: 'row', style: { marginTop: '8px' } }, find));
  }

  async function refreshMics({ quiet = true } = {}) {
    try {
      return await ctx.refreshMics();
    } catch (err) {
      if (!quiet) errToast(err);
      return [];
    }
  }

  function signature() {
    const parts = [];
    for (const key of ALL_KEYS) parts.push(value(key));
    parts.push((status.runtime?.mics || []).map((mic) => (
      typeof mic === 'string' ? mic : `${mic.name}:${mic.label || ''}`)).join('|'));
    const voice = status.runtime?.voice || {};
    parts.push(voice.activation?.oww?.available, voice.activation?.vosk?.available,
      voice.judge?.nemotron?.available, voice.tts?.available);
    return JSON.stringify(parts);
  }

  function drawAll(force = false) {
    const sig = signature();
    if (!force && sig === drawnSig) return;
    drawnSig = sig;
    drawActivation();
    drawJudge();
    drawVoice();
    drawRest();
  }

  const offStatus = ctx.onStatus((next) => {
    status = next;
    drawLive();
    drawAll();
  });
  status = ctx.status || status;
  drawLive();
  drawAll(true);
  if (!(status.runtime?.mics || []).length) refreshMics();

  return () => { offStatus(); };
}
