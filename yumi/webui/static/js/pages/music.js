import { api } from '../api.js';
import {
  el, tr, icon, errToast, toast, sectionTitle, emptyBox, badge, fmtDate,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let music = { now_playing: null, volume: null, source: null, mpd: false };
  let results = [];
  let searching = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Музыка' : 'Music' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Её библиотека и плеер в комнате: Jellyfin, свои папки или удалённый mpd.'
      : 'Her library and the room player: Jellyfin, your folders or a remote mpd.' }),
  );

  // ---------------------------------------------------------------- now playing
  const art = el('div', { class: 'now-art' }, icon('note', 40));
  const title = el('h2', { text: lang() === 'ru' ? 'Тишина' : 'Quiet' });
  const artist = el('div', { class: 'artist', text: '—' });
  const sourceBadge = el('span', { class: 'badge', text: '—' });
  const controls = el('div', { class: 'transport' });
  const nowCard = el('div', { class: 'card' },
    el('div', { class: 'now' }, art, el('div', {}, title, artist,
      el('div', { class: 'row', style: { marginTop: '6px' } }, sourceBadge))),
    controls);
  view.append(nowCard);

  const volume = el('input', { type: 'range', min: 0, max: 100, step: 5, value: 50 });
  const volumeOut = el('output', { class: 'mono', text: '50%' });
  const volumeCard = el('div', { class: 'card', style: { marginTop: '14px' } },
    el('h3', { text: lang() === 'ru' ? 'Громкость комнаты' : 'Room volume' }),
    el('div', { class: 'volume' }, icon('speaker', 18), volume, volumeOut));
  view.append(volumeCard);

  volume.addEventListener('input', () => { volumeOut.textContent = `${volume.value}%`; });
  volume.addEventListener('change', async () => {
    try {
      await api.post('/api/music/control', { cmd: `volume ${volume.value}` });
    } catch (err) { errToast(err); }
  });

  function transportButton(cmd, iconName, label) {
    return el('button', {
      class: 'btn', type: 'button', title: label,
      onclick: async () => {
        try {
          await api.post('/api/music/control', { cmd });
          await refresh();
        } catch (err) { errToast(err); }
      },
    }, icon(iconName, 16), el('span', { text: label }));
  }

  controls.append(
    transportButton('previous', 'prev', lang() === 'ru' ? 'Назад' : 'Prev'),
    transportButton('play_pause', 'play', lang() === 'ru' ? 'Играть' : 'Play'),
    transportButton('next', 'next', lang() === 'ru' ? 'Дальше' : 'Next'),
    transportButton('stop', 'stop', lang() === 'ru' ? 'Стоп' : 'Stop'),
  );

  // ---------------------------------------------------------------- search
  const kind = el('select', {},
    el('option', { value: 'song', text: lang() === 'ru' ? 'Песня' : 'Song' }),
    el('option', { value: 'artist', text: lang() === 'ru' ? 'Исполнитель' : 'Artist' }),
    el('option', { value: 'album', text: lang() === 'ru' ? 'Альбом' : 'Album' }),
    el('option', { value: 'random', text: lang() === 'ru' ? 'Сюрприз' : 'Surprise' }),
  );
  const query = el('input', {
    type: 'search', placeholder: lang() === 'ru' ? 'Название, исполнитель, альбом…' : 'Title, artist, album…',
    spellcheck: 'false',
  });
  const searchButton = el('button', {
    class: 'btn btn-primary', type: 'submit', text: lang() === 'ru' ? 'Найти' : 'Search',
  });
  const randomButton = el('button', {
    class: 'btn', type: 'button', text: lang() === 'ru' ? 'Что-нибудь' : 'Surprise me',
    onclick: () => play('random', ''),
  });
  const searchForm = el('form', { class: 'row' }, kind, query, searchButton, randomButton);
  const resultNote = el('div', { class: 'card-hint', text: '' });
  const list = el('div', { class: 'stack' });
  const searchCard = el('div', { class: 'card', style: { marginTop: '14px' } },
    el('h3', { text: lang() === 'ru' ? 'Библиотека' : 'Library' }),
    searchForm, resultNote, el('div', { style: { marginTop: '12px' } }, list));
  view.append(searchCard);

  searchForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    search();
  });
  query.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      search();
    }
  });

  async function search() {
    if (searching) return;
    const q = query.value.trim();
    if (kind.value !== 'random' && !q) return;
    searching = true;
    searchButton.disabled = true;
    resultNote.textContent = lang() === 'ru' ? 'ищу…' : 'searching…';
    list.replaceChildren();
    try {
      const data = await api.get(`/api/music/search?kind=${encodeURIComponent(kind.value)}&q=${encodeURIComponent(q)}`);
      results = data.tracks || [];
      resultNote.textContent = results.length
        ? `${results.length} · ${lang() === 'ru' ? 'источник' : 'source'}: ${data.source || '—'}`
        : (lang() === 'ru' ? 'ничего не нашла' : 'nothing found');
      drawResults();
    } catch (err) {
      resultNote.textContent = err.message;
      errToast(err);
    } finally {
      searching = false;
      searchButton.disabled = false;
    }
  }

  function drawResults() {
    list.replaceChildren();
    if (!results.length) {
      list.append(emptyBox(lang() === 'ru' ? 'Попробуй другое имя.' : 'Try another name.'));
      return;
    }
    for (const track of results) {
      const row = el('div', { class: 'track' },
        el('div', {},
          el('b', { text: track.title || (lang() === 'ru' ? 'без названия' : 'untitled') }),
          el('small', { text: [track.artist, track.album].filter(Boolean).join(' · ') || '—' })),
        el('div', { class: 'row' },
          el('span', { class: 'badge', text: track.source || '?' }),
          el('button', {
            class: 'btn btn-small btn-primary', type: 'button', text: lang() === 'ru' ? 'Играть' : 'Play',
            onclick: () => play('song', [track.artist, track.title].filter(Boolean).join(' ')),
          })),
      );
      list.append(row);
    }
  }

  async function play(what, q) {
    try {
      const data = await api.post('/api/music/play', { kind: what, query: q });
      const report = data.report || {};
      if (report.ok) {
        const track = report.track ? `${report.track.artist || ''} — ${report.track.title || ''}` : (lang() === 'ru' ? 'играет' : 'playing');
        toast(track, 'good');
      } else {
        toast(lang() === 'ru' ? 'Не нашла, что включить' : 'Nothing to play', 'warn');
      }
      await refresh();
    } catch (err) { errToast(err); }
  }

  // ---------------------------------------------------------------- status
  function draw() {
    const np = music.now_playing;
    if (Array.isArray(np) && (np[0] || np[1])) {
      artist.textContent = np[0] || (lang() === 'ru' ? 'без исполнителя' : 'unknown artist');
      title.textContent = np[1] || (lang() === 'ru' ? 'без названия' : 'untitled');
    } else {
      title.textContent = lang() === 'ru' ? 'Тишина' : 'Quiet';
      artist.textContent = lang() === 'ru' ? 'ничего не играет' : 'nothing is playing';
    }
    sourceBadge.textContent = music.source || '—';
    sourceBadge.className = 'badge mint';
    if (Number.isFinite(Number(music.volume))) {
      volume.value = String(music.volume);
      volumeOut.textContent = `${music.volume}%`;
    }
  }

  async function refresh() {
    try {
      music = await api.get('/api/music');
      draw();
    } catch (err) {
      // the live snapshot still carries now_playing; do not shout
    }
    ctx.refreshStatus();
  }

  const offStatus = ctx.onStatus((status) => {
    const np = status?.runtime?.music;
    if (!np) return;
    music = { ...music, ...np };
    draw();
  });
  refresh();
  return () => { offStatus(); };
}
