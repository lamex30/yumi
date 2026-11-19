import { api } from '../api.js';
import {
  el, tr, icon, errToast, toast, copyText, badge, emptyBox,
} from '../ui.js';

const LOGS = [
  ['yumi', 'Юми', 'Yumi'],
  ['judge', 'Судья', 'Judge'],
  ['ears', 'Ушки', 'Ears'],
  ['web', 'Панель', 'Panel'],
];

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let name = 'yumi';
  let lines = 300;
  let text = '';
  let auto = false;
  let timer = 0;
  let busy = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Журналы' : 'Logs' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Её собственный лог, дневник ушек, решения судьи и журнал доступа панели.'
      : 'Her own log, the ears’ diary, the judge’s decisions and the panel audit.' }),
  );

  const tabs = el('div', { class: 'log-tabs' });
  const path = el('code', { class: 'card-hint', text: '' });
  const linesSelect = el('select', { style: { maxWidth: '100px' } },
    ...[100, 300, 1000, 2000].map((n) => el('option', { value: String(n), text: String(n), selected: n === lines })));
  const refresh = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Обновить' : 'Refresh',
    onclick: () => load(true),
  });
  const autoButton = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Следить' : 'Follow',
    onclick: () => {
      auto = !auto;
      autoButton.classList.toggle('btn-on', auto);
      if (auto) startTimer();
      else stopTimer();
      toast(auto ? (lang() === 'ru' ? 'Обновляю каждые 4 секунды' : 'Refreshing every 4 seconds')
        : (lang() === 'ru' ? 'Автообновление выключено' : 'Auto refresh off'), auto ? 'good' : '');
    },
  });
  const copy = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Скопировать' : 'Copy',
    onclick: () => copyText(text),
  });
  const download = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Скачать' : 'Download',
    onclick: () => {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = el('a', { href: url, download: `${name}.log` });
      document.body.append(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });

  const terminal = el('pre', { class: 'terminal', text: lang() === 'ru' ? 'читаю…' : 'reading…' });

  view.append(
    el('div', { class: 'row', style: { marginBottom: '10px' } },
      tabs, linesSelect, refresh, autoButton, el('span', { class: 'spacer' }), copy, download),
    el('div', { class: 'row', style: { marginBottom: '8px' } }, path),
    terminal);

  for (const [id, ru, en] of LOGS) {
    const tab = el('button', {
      class: `tab ${id === name ? 'active' : ''}`.trim(),
      type: 'button',
      text: lang() === 'ru' ? ru : en,
      onclick: () => {
        name = id;
        for (const child of tabs.children) child.classList.remove('active');
        tab.classList.add('active');
        load(false);
      },
    });
    tabs.append(tab);
  }
  linesSelect.addEventListener('change', () => {
    lines = Number(linesSelect.value) || 300;
    load(true);
  });

  async function load(keepScroll) {
    if (busy) return;
    busy = true;
    const stick = terminal.scrollTop + terminal.clientHeight >= terminal.scrollHeight - 40;
    try {
      const data = await api.get(`/api/logs?name=${encodeURIComponent(name)}&lines=${lines}`);
      text = data.text || '';
      terminal.textContent = text || (lang() === 'ru' ? '(пусто)' : '(empty)');
      path.textContent = data.path || '';
      if (!keepScroll || stick) terminal.scrollTop = terminal.scrollHeight;
    } catch (err) {
      terminal.textContent = err.message;
      path.textContent = '';
    } finally {
      busy = false;
    }
  }

  function startTimer() {
    stopTimer();
    timer = window.setInterval(() => load(true), 4000);
  }

  function stopTimer() {
    if (timer) window.clearInterval(timer);
    timer = 0;
  }

  load(false);
  return () => { stopTimer(); };
}
