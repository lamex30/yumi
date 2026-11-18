import { api } from '../api.js';
import {
  el, tr, icon, errToast, toast, sectionTitle, buildSetting, emptyBox, badge,
} from '../ui.js';

const SOUND_KEYS = ['voice_kind', 'voice_vol', 'timer_sound'];

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let status = ctx.status || {};
  let micsSig = '';

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Ушки и голос' : 'Ears & voice' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Как она слышит имя, каким ухом слушает фразы и каким голосом отвечает.'
      : 'How she hears her name, which ear listens for phrases and which voice answers.' }),
  );

  // ---------------------------------------------------------------- live test
  const heard = el('div', { class: 'bubble', text: lang() === 'ru' ? 'Нажми «Слушать» и скажи что-нибудь.' : 'Press “Listen” and say something.' });
  const secs = el('input', { type: 'number', min: 2, max: 30, value: 8, style: { maxWidth: '80px' } });
  const speakInput = el('input', { type: 'text', placeholder: lang() === 'ru' ? 'Что ей сказать?' : 'What should she say?' });
  const testCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Проверка' : 'A quick test' }),
    el('div', { class: 'row' },
      secs,
      el('button', {
        class: 'btn btn-primary', type: 'button', text: lang() === 'ru' ? 'Слушать' : 'Listen',
        onclick: async () => {
          heard.textContent = lang() === 'ru' ? 'слушаю…' : 'listening…';
          try {
            const data = await api.post('/api/voice/listen', { seconds: Number(secs.value) || 8 });
            heard.textContent = data.heard
              ? `${lang() === 'ru' ? 'услышала' : 'heard'}: «${data.heard}»`
              : (lang() === 'ru' ? 'тишина' : 'silence');
          } catch (err) {
            heard.textContent = err.message;
            errToast(err);
          }
        },
      }),
      el('button', {
        class: 'btn btn-danger', type: 'button', text: lang() === 'ru' ? 'Закрыть ушки' : 'Stop listening',
        onclick: async () => {
          try {
            await api.post('/api/voice/stop');
            heard.textContent = lang() === 'ru' ? 'ушки закрыты' : 'ears closed';
          } catch (err) { errToast(err); }
        },
      })),
    el('div', { class: 'row', style: { marginTop: '10px' } },
      speakInput,
      el('button', {
        class: 'btn', type: 'button', text: lang() === 'ru' ? 'Сказать' : 'Speak',
        onclick: async () => {
          const text = speakInput.value.trim();
          if (!text) return;
          try {
            await api.post('/api/voice/speak', { text });
            toast(lang() === 'ru' ? 'Говорит' : 'Speaking', 'good');
          } catch (err) { errToast(err); }
        },
      })),
    el('div', { style: { marginTop: '12px' } }, heard));

  const live = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Прямо сейчас' : 'Right now' }),
    el('div', { class: 'card-hint', text: '' }));

  view.append(el('div', { class: 'grid two' }, testCard, live));

  // ---------------------------------------------------------------- settings
  const settingsBox = el('div', { class: 'card', style: { marginTop: '16px' } });
  view.append(sectionTitle(lang() === 'ru' ? 'Настройки ушек и голоса' : 'Ears and voice settings'));
  view.append(settingsBox);

  function entries() {
    const all = ctx.schema?.settings || [];
    const ears = all.filter((entry) => entry.page === 'voice' || entry.section === 'ears');
    const sound = all.filter((entry) => SOUND_KEYS.includes(entry.key));
    const seen = new Set();
    const out = [];
    for (const entry of [...ears, ...sound]) {
      if (seen.has(entry.key)) continue;
      seen.add(entry.key);
      const copy = { ...entry };
      if (entry.key === 'stt_mic') {
        const mics = status.runtime?.mics || [];
        copy.choices = [null, ...mics];
        copy.choice_labels = { ...(entry.choice_labels || {}) };
        for (const mic of mics) copy.choice_labels[mic] = mic;
      }
      if (entry.key === 'wake_ear') {
        // keep the schema choices; this only makes the current choice readable
      }
      out.push(copy);
    }
    return out;
  }

  function drawSettings() {
    settingsBox.replaceChildren();
    const list = entries();
    if (!list.length) {
      settingsBox.append(emptyBox(lang() === 'ru' ? 'Нет настроек' : 'No settings'));
      return;
    }
    for (const entry of list) {
      const value = status.settings?.[entry.key];
      settingsBox.append(buildSetting(entry, value, async (next) => {
        const data = await ctx.applySettings({ [entry.key]: next });
        if (data?.result?.saved) {
          status.settings = { ...(status.settings || {}), ...data.result.saved };
        }
      }));
    }
    const restart = list.some((entry) => entry.restart);
    if (restart) {
      settingsBox.append(el('p', { class: 'card-hint', text: lang() === 'ru'
        ? 'Масштаб окна применится после перезапуска.'
        : 'The window scale applies after a restart.' }));
    }
  }

  function draw() {
    const runtime = status.runtime || {};
    const mics = runtime.mics || [];
    const unavailable = runtime.unavailable || [];
    const liveText = [];
    liveText.push(el('div', { class: 'row' },
      el('span', { class: 'dot', style: { background: runtime.wake_on ? 'var(--mint)' : 'var(--red)' } }),
      el('span', { text: runtime.wake_on
        ? (lang() === 'ru' ? 'имя слышит' : 'listening for her name')
        : (lang() === 'ru' ? 'имя не слышит' : 'name listening is off') })));
    if (runtime.sleeping) liveText.push(badge(lang() === 'ru' ? 'не мешать' : 'dnd', 'amber'));
    if (runtime.listening) liveText.push(badge(lang() === 'ru' ? 'слушает фразу' : 'listening', 'mint'));
    if (runtime.busy) liveText.push(badge(lang() === 'ru' ? 'занята' : 'busy', 'amber'));
    if (unavailable.length) {
      liveText.push(el('div', { class: 'card-hint', text: `${lang() === 'ru' ? 'недоступно' : 'unavailable'}: ${unavailable.join(', ')}` }));
    }
    live.replaceChildren(el('h3', { text: lang() === 'ru' ? 'Прямо сейчас' : 'Right now' }));
    live.append(el('div', { class: 'stack' }, ...liveText));
    if (mics.length) {
      live.append(el('div', { class: 'card-hint', style: { marginTop: '10px' }, text: `${lang() === 'ru' ? 'микрофоны' : 'microphones'}:` }));
      const list = el('div', { class: 'stack', style: { marginTop: '4px' } });
      for (const mic of mics) {
        list.append(el('button', {
          class: 'btn btn-small', type: 'button', text: mic,
          title: lang() === 'ru' ? 'выбрать этот микрофон' : 'use this microphone',
          onclick: async () => {
            await ctx.applySettings({ stt_mic: mic });
            status.settings = { ...(status.settings || {}), stt_mic: mic };
            drawSettings();
          },
        }));
      }
      live.append(list);
    }
    const sig = mics.join('|');
    if (sig !== micsSig) {
      micsSig = sig;
      drawSettings();
    }
  }

  const offStatus = ctx.onStatus((next) => {
    status = next;
    draw();
  });
  status = ctx.status || status;
  draw();
  drawSettings();

  return () => { offStatus(); };
}
