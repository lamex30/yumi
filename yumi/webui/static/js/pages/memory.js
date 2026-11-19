import { api } from '../api.js';
import {
  el, clear, tr, icon, fmtDate, errToast, toast, confirmDialog, emptyBox, badge,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let items = [];
  let busy = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Память' : 'Memory' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Дневник разговоров в sqlite. Здесь видно, что Юми помнит, и можно это стереть.'
      : 'The talk diary in sqlite. See what Yumi remembers, and erase it.' }),
  );

  const search = el('input', {
    type: 'search', placeholder: lang() === 'ru' ? 'Найти в дневнике…' : 'Search the diary…',
    spellcheck: 'false',
  });
  const limit = el('select', {},
    ...[20, 50, 100, 200].map((n) => el('option', { value: String(n), text: String(n), selected: n === 50 })));
  const resultNote = el('span', { class: 'card-hint', text: '' });
  const queryButton = el('button', {
    class: 'btn btn-primary', type: 'button', text: lang() === 'ru' ? 'Искать' : 'Search',
    onclick: () => load(true),
  });
  const refreshButton = el('button', {
    class: 'btn', type: 'button', text: lang() === 'ru' ? 'Обновить' : 'Refresh',
    onclick: () => { search.value = ''; load(false); },
  });
  const forgetAll = el('button', {
    class: 'btn btn-danger btn-small', type: 'button', text: lang() === 'ru' ? 'Забыть всё' : 'Forget everything',
    onclick: async () => {
      if (!await confirmDialog({
        title: lang() === 'ru' ? 'Стереть весь дневник?' : 'Erase the whole diary?',
        message: lang() === 'ru'
          ? 'Юми перестанет помнить старые разговоры. Это необратимо.'
          : 'Yumi will stop remembering older talks. This cannot be undone.',
        confirmLabel: lang() === 'ru' ? 'Стереть' : 'Erase', danger: true,
      })) return;
      await clearMemory('all');
    },
  });
  const forgetSassy = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Забыть 18+' : 'Forget 18+',
    title: lang() === 'ru' ? 'Стереть строки «Дерзкой 18+»' : 'Erase the sassy 18+ lines',
    onclick: async () => {
      if (!await confirmDialog({
        title: lang() === 'ru' ? 'Стереть строки 18+?' : 'Erase the 18+ lines?',
        message: lang() === 'ru'
          ? 'Пропадут только реплики, сказанные в дерзком режиме.'
          : 'Only the lines said in the sassy mode go.',
        confirmLabel: lang() === 'ru' ? 'Стереть' : 'Erase', danger: true,
      })) return;
      await clearMemory('sassy');
    },
  });

  const toolbar = el('div', { class: 'row', style: { marginBottom: '14px' } },
    search, limit, queryButton, refreshButton, el('span', { class: 'spacer' }),
    resultNote, forgetSassy, forgetAll);
  const list = el('div', { class: 'stack' });
  view.append(toolbar, list);

  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      load(true);
    }
  });

  async function load(useQuery) {
    if (busy) return;
    busy = true;
    const q = useQuery ? search.value.trim() : '';
    resultNote.textContent = lang() === 'ru' ? 'читаю…' : 'reading…';
    try {
      const params = new URLSearchParams({ limit: limit.value });
      if (q) params.set('q', q);
      const data = await api.get(`/api/memory?${params}`);
      items = Array.isArray(data.items) ? data.items : [];
      resultNote.textContent = q
        ? `${lang() === 'ru' ? 'поиск' : 'search'}: «${q}» · ${items.length}`
        : `${items.length} ${lang() === 'ru' ? 'строк' : 'lines'}`;
      draw();
    } catch (err) {
      resultNote.textContent = err.message;
      errToast(err);
    } finally {
      busy = false;
    }
  }

  function draw() {
    clear(list);
    if (!items.length) {
      list.append(emptyBox(lang() === 'ru' ? 'Дневник пуст.' : 'The diary is empty.'));
      return;
    }
    for (const item of items) {
      const node = el('div', { class: `memory-item ${item.role || ''}`.trim() },
        el('div', { class: 'meta' },
          el('span', { text: item.role || '—' }),
          el('span', { text: fmtDate(item.ts) }),
          item.tag ? badge(item.tag, 'amber') : null),
        el('div', { text: item.text }));
      list.append(node);
    }
  }

  async function clearMemory(tag) {
    try {
      const data = await api.post('/api/memory/clear', { tag });
      toast(`${lang() === 'ru' ? 'Стёрто' : 'Erased'}: ${data.deleted}`, 'good');
      await load(false);
    } catch (err) { errToast(err); }
  }

  const onStatus = (next) => {
    if (next?.settings?.sassy) forgetSassy.classList.add('btn-on');
    else forgetSassy.classList.remove('btn-on');
  };
  const offStatus = ctx.onStatus(onStatus);
  onStatus(ctx.status);
  load(false);

  return () => { offStatus(); };
}
