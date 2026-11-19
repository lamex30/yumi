import { api } from '../api.js';
import {
  el, clear, tr, icon, fmtClock, errToast, toast, confirmDialog, sectionTitle, emptyBox,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let sending = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Разговор' : 'Talk' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Тот же ум и тот же дневник, что у Юми на экране. Здесь можно говорить подолгу — и она запомнит.'
      : 'The same mind and the same diary as the Yumi on your screen. Talk at length — she remembers.' }),
  );

  const log = el('div', { class: 'chat-log' });
  const input = el('textarea', {
    placeholder: lang() === 'ru' ? 'Скажи Юми что-нибудь…' : 'Say something to Yumi…',
    rows: 2,
    spellcheck: 'false',
  });
  const send = el('button', { class: 'btn btn-primary', type: 'submit', text: lang() === 'ru' ? 'Сказать' : 'Send' });
  const form = el('form', { class: 'chat-form' }, input, send);
  const chat = el('div', { class: 'chat pixel-frame' }, log, form);

  const bubble = el('div', { class: 'bubble', text: lang() === 'ru' ? '…' : '…' });
  const liveCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Её реплика' : 'Her line' }),
    bubble,
    el('p', { class: 'card-hint', style: { marginTop: '12px' }, text: lang() === 'ru'
      ? 'Это то, что она говорит вслух в мире, не ответ в чате.'
      : 'This is what she says aloud in the world, not the chat answer.' }));

  const prompts = lang() === 'ru'
    ? ['Как ты?', 'Что ты умеешь?', 'Расскажи что-нибудь', 'Помоги с планом на день']
    : ['How are you?', 'What can you do?', 'Tell me something', 'Help me plan the day'];
  const promptCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Начать' : 'Starters' }),
    el('div', { class: 'stack' }, ...prompts.map((text) => el('button', {
      class: 'btn prompt-chip', type: 'button', text,
      onclick: () => {
        input.value = text;
        input.focus();
      },
    }))));

  const askInput = el('textarea', {
    rows: 2,
    placeholder: lang() === 'ru' ? 'Фраза, которую она услышит и выполнит…' : 'A phrase she will hear and act on…',
  });
  const askOut = el('div', { class: 'card-hint', text: '' });
  const askCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Сказать в мир' : 'Say into the world' }),
    el('p', { class: 'card-hint', text: lang() === 'ru'
      ? 'Будто ты сказал это голосом: она проснётся, ответит и покажет пузырь.'
      : 'As if you said it aloud: she wakes, answers and shows a bubble.' }),
    askInput,
    el('div', { class: 'row', style: { marginTop: '8px' } },
      el('button', {
        class: 'btn btn-primary btn-small', type: 'button', text: lang() === 'ru' ? 'Позвать' : 'Call',
        onclick: async () => {
          const text = askInput.value.trim();
          if (!text) return;
          askOut.textContent = lang() === 'ru' ? 'зову…' : 'calling…';
          try {
            const data = await api.post('/api/ask', { text, speak: true });
            askOut.textContent = data.queued
              ? (lang() === 'ru' ? 'она услышала' : 'she heard it')
              : (data.reason || '…');
            askInput.value = '';
            ctx.refreshStatus();
          } catch (err) {
            askOut.textContent = err.message;
            errToast(err);
          }
        },
      }),
      el('button', {
        class: 'btn btn-small btn-ghost', type: 'button', text: lang() === 'ru' ? 'Очистить' : 'Clear',
        onclick: () => { askInput.value = ''; askOut.textContent = ''; },
      })),
    askOut);

  const speakInput = el('input', {
    type: 'text', placeholder: lang() === 'ru' ? 'Что ей произнести?' : 'What should she say?',
  });
  const listenSecs = el('input', { type: 'number', min: 2, max: 30, value: 8, style: { maxWidth: '76px' } });
  const voiceOut = el('div', { class: 'card-hint', text: '' });
  const voiceCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Голос и ушки' : 'Voice and ears' }),
    el('div', { class: 'row' },
      speakInput,
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Сказать' : 'Speak',
        onclick: async () => {
          const text = speakInput.value.trim();
          if (!text) return;
          try {
            await api.post('/api/voice/speak', { text });
            voiceOut.textContent = lang() === 'ru' ? 'говорит…' : 'speaking…';
          } catch (err) { voiceOut.textContent = err.message; }
        },
      })),
    el('div', { class: 'row', style: { marginTop: '8px' } },
      el('span', { class: 'card-hint', text: lang() === 'ru' ? 'Слушать' : 'Listen' }),
      listenSecs,
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Слушать' : 'Listen',
        onclick: async () => {
          voiceOut.textContent = lang() === 'ru' ? 'слушаю…' : 'listening…';
          try {
            const data = await api.post('/api/voice/listen', { seconds: Number(listenSecs.value) || 8 });
            voiceOut.textContent = data.heard
              ? `${lang() === 'ru' ? 'услышала' : 'heard'}: «${data.heard}»`
              : (lang() === 'ru' ? 'тишина' : 'silence');
          } catch (err) { voiceOut.textContent = err.message; }
        },
      }),
      el('button', {
        class: 'btn btn-small btn-danger', type: 'button', text: lang() === 'ru' ? 'Стоп' : 'Stop',
        onclick: async () => {
          try {
            await api.post('/api/voice/stop');
            voiceOut.textContent = lang() === 'ru' ? 'остановила' : 'stopped';
          } catch (err) { voiceOut.textContent = err.message; }
        },
      })),
    voiceOut);

  const side = el('div', { class: 'chat-side' }, liveCard, promptCard, askCard, voiceCard);

  const clearBtn = el('button', {
    class: 'btn btn-small btn-danger', type: 'button', text: lang() === 'ru' ? 'Стереть этот чат' : 'Clear this chat',
    onclick: async () => {
      if (!await confirmDialog({
        title: lang() === 'ru' ? 'Стереть разговор?' : 'Clear the talk?',
        message: lang() === 'ru'
          ? 'Пропадёт только история этой панели. Дневник памяти останется.'
          : 'Only this panel’s history goes. The memory diary stays.',
        confirmLabel: lang() === 'ru' ? 'Стереть' : 'Clear', danger: true,
      })) return;
      try {
        await api.del('/api/chat/history');
        log.replaceChildren(emptyBox(lang() === 'ru' ? 'Чистый лист' : 'A clean page'));
      } catch (err) { errToast(err); }
    },
  });

  view.append(el('div', { class: 'chat-shell' }, chat, side));
  view.append(el('div', { class: 'row', style: { marginTop: '12px' } }, clearBtn));

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  }

  function messageNode(role, text, ts) {
    const node = el('div', { class: `msg ${role}` }, text);
    if (ts) node.append(el('span', { class: 'meta', text: fmtClock(ts) }));
    return node;
  }

  function addMessage(role, text, ts = Date.now() / 1000) {
    const stick = nearBottom();
    log.append(messageNode(role, text, ts));
    if (stick) log.scrollTop = log.scrollHeight;
  }

  async function loadHistory() {
    clear(log);
    try {
      const data = await api.get('/api/chat/history');
      if (!data.messages?.length) {
        log.append(emptyBox(lang() === 'ru'
          ? 'Пока тихо. Напиши первой — она ответит.'
          : 'Quiet so far. Write first — she will answer.'));
      } else {
        for (const m of data.messages) addMessage(m.role, m.text, m.ts);
        log.scrollTop = log.scrollHeight;
      }
    } catch (err) {
      log.append(emptyBox(err.message));
    }
  }

  async function submit() {
    const text = input.value.trim();
    if (!text || sending) return;
    sending = true;
    send.disabled = true;
    input.value = '';
    input.style.height = '';
    const empty = log.querySelector('.empty');
    if (empty) empty.remove();
    addMessage('user', text);
    const typing = el('div', { class: 'msg assistant typing', text: lang() === 'ru' ? 'думает' : 'thinking' });
    log.append(typing);
    log.scrollTop = log.scrollHeight;
    try {
      const data = await api.post('/api/chat', { text });
      typing.remove();
      addMessage('assistant', data.reply || (lang() === 'ru' ? '…' : '…'));
      ctx.refreshStatus();
    } catch (err) {
      typing.remove();
      addMessage('system', `${lang() === 'ru' ? 'ошибка' : 'error'}: ${err.message}`);
      errToast(err);
    } finally {
      sending = false;
      send.disabled = false;
      input.focus();
    }
  }

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    submit();
  });
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey) {
      ev.preventDefault();
      submit();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(150, input.scrollHeight)}px`;
  });

  const offBubble = ctx.onEvent('bubble', (payload) => {
    bubble.textContent = payload?.text || (lang() === 'ru' ? 'молчит' : 'quiet');
  });
  const offChat = ctx.onEvent('chat', () => {
    // another tab answered; the local flow already drew its own message
  });

  const current = ctx.status?.runtime?.bubble;
  if (current) bubble.textContent = current;
  loadHistory();
  input.focus();

  return () => {
    offBubble();
    offChat();
  };
}
