import { api } from '../api.js';
import {
  el, tr, icon, fmtTime, fmtUptime, fmtBytes, toast, errToast, confirmDialog,
  sectionTitle, emptyBox, badge,
} from '../ui.js';

export function render(view, ctx) {
  let status = ctx.status || {};
  let timerBase = status.runtime?.timer || null;
  let timerAt = Date.now();
  const lang = () => ctx.lang;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Обзор' : 'Overview' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Всё, что Юми делает прямо сейчас: её реплика, таймер, музыка и последние файлы.'
      : 'Everything Yumi is doing right now: her line, the timer, music and the latest files.' }),
  );

  // ---------------------------------------------------------------- hero
  const bubble = el('div', { class: 'bubble', text: '…' });
  const stateLine = el('div', { class: 'state-line', text: lang() === 'ru' ? 'подключаюсь…' : 'connecting…' });
  const heroFace = el('div', { class: 'hero-art' }, el('img', { src: '/art/icon', alt: '', width: 128, height: 128 }));
  const hero = el('div', { class: 'hero card pixel-frame' },
    heroFace,
    el('div', {},
      el('h1', { text: lang() === 'ru' ? 'Юми' : 'Yumi' }),
      stateLine,
      bubble));
  view.append(hero);

  // ---------------------------------------------------------------- stats
  const stat = (label) => {
    const value = el('div', { class: 'value', text: '—' });
    const sub = el('div', { class: 'sub', text: '' });
    const card = el('div', { class: 'stat card' },
      el('div', { class: 'label', text: label }), value, sub);
    return { card, value, sub };
  };
  const statMode = stat(lang() === 'ru' ? 'Режим' : 'Mode');
  const statUp = stat(lang() === 'ru' ? 'В сети' : 'Uptime');
  const statShelf = stat(lang() === 'ru' ? 'На полке' : 'On the shelf');
  const statJobs = stat(lang() === 'ru' ? 'Очередь' : 'Queue');
  view.append(el('div', { class: 'grid four', style: { marginTop: '14px' } },
    statMode.card, statUp.card, statShelf.card, statJobs.card));

  // ---------------------------------------------------------------- timer + music
  const timerBig = el('div', { class: 'timer-big', text: '00:00' });
  const timerState = el('div', { class: 'card-hint', text: lang() === 'ru' ? 'таймер не заведён' : 'no timer' });
  const timerButtons = el('div', { class: 'transport' });
  const minutes = el('input', {
    type: 'number', min: 1, max: 1440, step: 1, value: 25,
    style: { maxWidth: '90px' },
  });
  const timerCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Таймер' : 'Timer' }),
    timerBig, timerState,
    el('div', { class: 'row', style: { marginTop: '10px' } },
      el('label', { class: 'field', style: { flex: '0 0 100px' } },
        el('span', { text: lang() === 'ru' ? 'Минуты' : 'Minutes' }), minutes),
      el('button', {
        class: 'btn btn-primary btn-small', type: 'button',
        text: lang() === 'ru' ? 'Завести' : 'Start',
        onclick: () => timerCmd('set', Math.max(1, Number(minutes.value) || 25) * 60),
      }),
      ...[5, 15, 25, 60].map((m) => el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? `${m}м` : `${m}m`,
        onclick: () => timerCmd('set', m * 60),
      }))),
    el('div', { class: 'transport' }, timerButtons));

  const nowTitle = el('h2', { text: lang() === 'ru' ? 'Тишина' : 'Quiet' });
  const nowArtist = el('div', { class: 'artist', text: '—' });
  const nowCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Сейчас играет' : 'Now playing' }),
    el('div', { class: 'now' },
      el('div', { class: 'now-art' }, icon('note', 40)),
      el('div', {}, nowTitle, nowArtist)),
    el('div', { class: 'transport' },
      musicBtn('prev', 'prev'),
      musicBtn('play_pause', 'play', lang() === 'ru' ? 'играть / пауза' : 'play / pause'),
      musicBtn('next', 'next'),
      musicBtn('stop', 'stop'),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Тише' : 'Quieter',
        onclick: () => musicCmd('volume_down'),
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Громче' : 'Louder',
        onclick: () => musicCmd('volume_up'),
      })));

  view.append(el('div', { class: 'grid two', style: { marginTop: '14px' } }, timerCard, nowCard));

  function musicBtn(cmd, iconName, title) {
    const b = el('button', { class: 'btn btn-small btn-icon', type: 'button', title: title || cmd });
    b.append(icon(iconName, 13));
    b.addEventListener('click', () => musicCmd(cmd));
    return b;
  }

  async function musicCmd(cmd) {
    try {
      await api.post('/api/music/control', { cmd });
      await ctx.refreshStatus();
    } catch (err) {
      errToast(err);
    }
  }

  async function timerCmd(action, seconds) {
    try {
      const data = await api.post('/api/timer', { action, seconds });
      if (data.timer) {
        timerBase = data.timer;
        timerAt = Date.now();
        drawTimer();
      }
      await ctx.refreshStatus();
    } catch (err) {
      errToast(err);
    }
  }

  // ---------------------------------------------------------------- quick actions
  const quick = el('div', { class: 'quick' });
  const showBtn = el('button', { class: 'btn btn-primary', type: 'button' });
  const pinBtn = el('button', { class: 'btn', type: 'button', text: lang() === 'ru' ? 'Закрепить' : 'Pin' });
  const dndBtn = el('button', { class: 'btn', type: 'button', text: 'DND' });

  showBtn.addEventListener('click', async () => {
    await ctx.stateAction('toggle');
  });
  pinBtn.addEventListener('click', async () => {
    await ctx.applySettings({ pinned: !(ctx.status?.settings?.pinned) }, { quiet: false });
    await ctx.refreshStatus();
  });
  dndBtn.addEventListener('click', async () => {
    await ctx.stateAction('dnd', { on: !ctx.status?.runtime?.sleeping });
  });

  quick.append(
    showBtn, pinBtn, dndBtn,
    actionButton('Домой', 'home', () => ctx.stateAction('home')),
    actionButton('Экран', 'monitor', () => ctx.stateAction('monitor')),
    actionButton('Пипетка', 'pipette', () => ctx.stateAction('pipette')),
    actionButton('Недавние', 'recent', () => ctx.stateAction('recent')),
    actionButton('Полка', 'shelf', () => ctx.stateAction('shelf')),
    actionButton('Звук', 'sound', () => ctx.stateAction('sound')),
    actionButton('Тур', 'tour', () => ctx.stateAction('tour')),
    actionButton('Конфиги', 'config-dir', () => ctx.stateAction('config-dir')),
    actionButton('Перезапуск', 'restart', async () => {
      const ru = lang() === 'ru';
      if (await confirmDialog({
        title: ru ? 'Перезапуск' : 'Restart',
        message: ru ? 'Юми закроется и откроется снова. Панель ненадолго пропадёт.'
          : 'Yumi closes and opens again. The panel disappears for a moment.',
        confirmLabel: ru ? 'Перезапустить' : 'Restart',
      })) {
        await ctx.stateAction('restart');
      }
    }, 'btn-danger'),
    actionButton('Выход', 'quit', async () => {
      const ru = lang() === 'ru';
      if (await confirmDialog({
        title: ru ? 'Выход' : 'Quit',
        message: ru ? 'Юми закроется совсем. Панель перестанет отвечать.'
          : 'Yumi closes for good. The panel stops answering.',
        confirmLabel: ru ? 'Закрыть Юми' : 'Quit Yumi', danger: true,
      })) {
        await ctx.stateAction('quit');
      }
    }, 'btn-danger'),
  );

  function actionButton(label, action, fn, kind = '') {
    const names = {
      'Домой': 'Home', 'Экран': 'Monitor', 'Пипетка': 'Pipette', 'Недавние': 'Recent',
      'Полка': 'Shelf', 'Звук': 'Sound', 'Тур': 'Tour', 'Конфиги': 'Configs',
      'Перезапуск': 'Restart', 'Выход': 'Quit',
    };
    return el('button', {
      class: `btn ${kind}`.trim(), type: 'button',
      text: lang() === 'ru' ? label : (names[label] || label),
      onclick: fn,
    });
  }

  const quickCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Быстро' : 'Quick actions' }),
    el('p', { class: 'card-hint', text: lang() === 'ru'
      ? 'Это те же действия, что в кольце и меню Юми.'
      : 'The same actions as her ring and menu.' }),
    quick);

  // ---------------------------------------------------------------- small tools
  const toolOut = el('div', { class: 'bubble', text: lang() === 'ru' ? 'Спроси что-нибудь у маленьких часов.' : 'Ask the little clock something.' });
  const toolCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Мелочи' : 'Small things' }),
    el('div', { class: 'row' },
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Время' : 'Time',
        onclick: async () => {
          try {
            const d = await api.get('/api/tools/time');
            toolOut.textContent = `${d.time} · ${d.stamp}`;
          } catch (err) { errToast(err); }
        },
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Шутка' : 'Joke',
        onclick: async () => {
          try {
            const d = await api.get('/api/tools/joke');
            toolOut.textContent = d.joke || '…';
          } catch (err) { errToast(err); }
        },
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Погода' : 'Weather',
        onclick: async () => {
          try {
            const d = await api.get('/api/tools/weather');
            toolOut.textContent = d.report || '…';
          } catch (err) { errToast(err); }
        },
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Параметры' : 'Params',
        onclick: async () => {
          try {
            const d = await api.get('/api/tools/params');
            toolOut.textContent = d.report || '…';
          } catch (err) { errToast(err); }
        },
      })),
    el('div', { style: { marginTop: '12px' } }, toolOut));

  view.append(sectionTitle(lang() === 'ru' ? 'Действия' : 'Actions'));
  view.append(el('div', { class: 'grid two' }, quickCard, toolCard));

  // ---------------------------------------------------------------- recent files
  const filesStrip = el('div', { class: 'file-strip' });
  view.append(sectionTitle(lang() === 'ru' ? 'Недавние файлы' : 'Recent files'));
  view.append(filesStrip);

  async function loadFiles() {
    try {
      const data = await api.get('/api/files/recent');
      filesStrip.replaceChildren();
      const items = (data.items || []).slice(0, 12);
      if (!items.length) {
        filesStrip.append(emptyBox(lang() === 'ru' ? 'Пока пусто' : 'Nothing yet'));
        return;
      }
      for (const file of items) {
        const card = el('div', { class: 'mini-file', title: file.path });
        if (file.kind === 'image' && file.exists) {
          card.append(el('img', { src: `/api/file/preview?path=${encodeURIComponent(file.path)}`, alt: '', loading: 'lazy' }));
        } else {
          card.append(el('div', {
            style: { height: '74px', display: 'grid', placeItems: 'center', background: '#15111f', marginBottom: '6px', color: 'var(--dim)' },
          }, el('span', { text: (file.ext || file.kind || 'file').toUpperCase().slice(0, 6) })));
        }
        card.append(
          el('b', { text: file.name }),
          el('small', { text: `${fmtBytes(file.size)} · ${file.kind}` }),
        );
        card.addEventListener('click', async () => {
          try {
            await api.post('/api/files/open', { path: file.path });
            toast(lang() === 'ru' ? 'Открываю' : 'Opening', 'good');
          } catch (err) { errToast(err); }
        });
        filesStrip.append(card);
      }
    } catch (err) {
      filesStrip.replaceChildren(emptyBox(err.message));
    }
  }

  // ---------------------------------------------------------------- live drawing
  function remaining() {
    if (!timerBase) return 0;
    if (!timerBase.running) return Number(timerBase.seconds) || 0;
    return Math.max(0, Number(timerBase.seconds) - Math.floor((Date.now() - timerAt) / 1000));
  }

  function drawTimer() {
    const tm = timerBase;
    const left = remaining();
    timerBig.textContent = fmtTime(left);
    timerBig.classList.toggle('done', Boolean(tm?.done));
    let note = lang() === 'ru' ? 'таймер не заведён' : 'no timer';
    if (tm?.done) note = lang() === 'ru' ? 'время вышло' : 'time is up';
    else if (tm?.paused) note = lang() === 'ru' ? 'на паузе' : 'paused';
    else if (tm?.pending) note = lang() === 'ru' ? 'вот-вот начнётся' : 'about to start';
    else if (tm?.running) note = lang() === 'ru' ? 'идёт' : 'running';
    timerState.textContent = note;

    timerButtons.replaceChildren();
    if (tm?.running || tm?.paused) {
      timerButtons.append(el('button', {
        class: 'btn btn-small', type: 'button',
        text: tm.paused ? (lang() === 'ru' ? 'Продолжить' : 'Resume') : (lang() === 'ru' ? 'Пауза' : 'Pause'),
        onclick: () => timerCmd(tm.paused ? 'resume' : 'pause'),
      }));
    }
    timerButtons.append(
      el('button', {
        class: 'btn btn-small', type: 'button', text: '+5',
        onclick: () => timerCmd('add', 300),
      }),
      el('button', {
        class: 'btn btn-small btn-danger', type: 'button', text: lang() === 'ru' ? 'Стоп' : 'Stop',
        onclick: () => timerCmd('stop'),
      }),
    );
  }

  function draw(s) {
    status = s || status;
    const runtime = status.runtime || {};
    const settings = status.settings || {};
    const tm = runtime.timer;
    if (tm) {
      timerBase = tm;
      timerAt = Date.now();
    }
    drawTimer();

    if (runtime.bubble) bubble.textContent = runtime.bubble;
    else bubble.textContent = settings.sassy
      ? (lang() === 'ru' ? 'Тишина. Подозрительно.' : 'Quiet. Suspicious.')
      : (lang() === 'ru' ? 'Я здесь. Скажи что-нибудь или заведи таймер.' : 'I am here. Say something or set a timer.');

    const mode = runtime.mode === 'air' ? (lang() === 'ru' ? 'в воздухе' : 'in the air')
      : (lang() === 'ru' ? 'у края экрана' : 'at the edge');
    const where = runtime.out ? (lang() === 'ru' ? 'снаружи' : 'out')
      : (lang() === 'ru' ? 'спрятана' : 'hidden');
    const mood = runtime.sleeping ? (lang() === 'ru' ? 'не мешать' : 'dnd')
      : runtime.thinking ? (lang() === 'ru' ? 'думает' : 'thinking')
        : runtime.listening ? (lang() === 'ru' ? 'слушает' : 'listening')
          : where;
    stateLine.textContent = `${mode} · ${mood} · ${fmtUptime(status.uptime)}`;

    statMode.value.textContent = runtime.mode === 'air'
      ? (lang() === 'ru' ? 'воздух' : 'air')
      : (lang() === 'ru' ? 'док' : 'dock');
    statMode.sub.textContent = runtime.sleeping ? (lang() === 'ru' ? 'не мешать' : 'do not disturb')
      : runtime.out ? (lang() === 'ru' ? 'гуляет' : 'out') : (lang() === 'ru' ? 'рядом' : 'here');
    statUp.value.textContent = fmtUptime(status.uptime);
    statUp.sub.textContent = `v${status.version || '?'} · ${runtime.face || '—'}`;
    statShelf.value.textContent = String(runtime.shelf_count ?? 0);
    statShelf.sub.textContent = lang() === 'ru' ? 'файлов на полке' : 'files on the shelf';
    const jobs = status.jobs || {};
    statJobs.value.textContent = jobs.busy ? `${Math.round((jobs.current?.fraction || 0) * 100)}%` : '0';
    statJobs.sub.textContent = jobs.current
      ? `${jobs.current.op} · ${jobs.queued?.length || 0} ${lang() === 'ru' ? 'в очереди' : 'queued'}`
      : (lang() === 'ru' ? 'свободна' : 'idle');

    showBtn.textContent = runtime.out
      ? (lang() === 'ru' ? 'Спрятать Юми' : 'Hide Yumi')
      : (lang() === 'ru' ? 'Показать Юми' : 'Show Yumi');
    pinBtn.classList.toggle('btn-on', Boolean(settings.pinned));
    pinBtn.textContent = settings.pinned
      ? (lang() === 'ru' ? 'Открепить' : 'Unpin')
      : (lang() === 'ru' ? 'Закрепить' : 'Pin');
    dndBtn.classList.toggle('btn-on', Boolean(runtime.sleeping));

    const music = runtime.music?.now_playing;
    if (Array.isArray(music) && (music[0] || music[1])) {
      nowArtist.textContent = music[0] || (lang() === 'ru' ? 'без исполнителя' : 'unknown artist');
      nowTitle.textContent = music[1] || (lang() === 'ru' ? 'без названия' : 'untitled');
    } else {
      nowTitle.textContent = lang() === 'ru' ? 'Тишина' : 'Quiet';
      nowArtist.textContent = lang() === 'ru' ? 'ничего не играет' : 'nothing is playing';
    }
    nowCard.querySelector('h3').textContent = runtime.music?.volume != null
      ? `${lang() === 'ru' ? 'Сейчас играет' : 'Now playing'} · ${runtime.music.volume}%`
      : (lang() === 'ru' ? 'Сейчас играет' : 'Now playing');
  }

  draw(ctx.status);
  const offStatus = ctx.onStatus(draw);
  const offFiles = ctx.onEvent('files', loadFiles);
  loadFiles();
  const tick = window.setInterval(drawTimer, 500);

  return () => {
    offStatus();
    offFiles();
    window.clearInterval(tick);
  };
}
