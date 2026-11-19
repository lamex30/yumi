// Tiny DOM and drawing helpers.  Everything is built with createElement and
// textContent: no user or Yumi string is ever pasted as HTML.

export const UI = { lang: 'ru' };
let toastTimer = 0;

export function setLang(lang) {
  UI.lang = lang === 'en' ? 'en' : 'ru';
  document.documentElement.lang = UI.lang;
}

export function tr(value) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') {
    return value[UI.lang] || value.en || value.ru || Object.values(value)[0] || '';
  }
  return String(value);
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === 'class' || key === 'className') {
      node.className = value;
    } else if (key === 'text') {
      node.textContent = value;
    } else if (key === 'dataset') {
      for (const [dk, dv] of Object.entries(value)) {
        if (dv != null) node.dataset[dk] = String(dv);
      }
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key === 'value') {
      node.value = value;
    } else if (key === 'checked' || key === 'disabled' || key === 'selected') {
      node[key] = Boolean(value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) {
      node.setAttribute(key, '');
    } else {
      node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(4)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---------------------------------------------------------------- icons
const ICON_PATHS = {
  home: 'M3 11 L12 3 L21 11 M6 10 V21 H18 V10',
  chat: 'M4 4 H20 V16 H9 L4 21 Z',
  box: 'M4 7 L12 3 L20 7 V17 L12 21 L4 17 Z M4 7 L12 11 L20 7 M12 11 V21',
  note: 'M9 18 V5 L20 3 V16 M9 18 A3 3 0 1 1 3 18 A3 3 0 1 1 9 18 M20 16 A3 3 0 1 1 14 16 A3 3 0 1 1 20 16',
  pencil: 'M4 20 L5 15 L16 4 L20 8 L9 19 Z M14 6 L18 10',
  mic: 'M12 3 A3 3 0 0 1 15 6 V12 A3 3 0 0 1 9 12 V6 A3 3 0 0 1 12 3 Z M5 11 A7 7 0 0 0 19 11 M12 18 V21 M8 21 H16',
  grid: 'M4 4 H10 V10 H4 Z M14 4 H20 V10 H14 Z M4 14 H10 V20 H4 Z M14 14 H20 V20 H14 Z',
  palette: 'M12 3 A9 9 0 1 0 12 21 A2 2 0 0 0 14 19 A2 2 0 0 1 16 17 H18 A3 3 0 0 0 21 14 A9 9 0 0 0 12 3 Z M7 11 H7.1 M10 7 H10.1 M15 7 H15.1 M17 11 H17.1',
  gear: 'M12 8 A4 4 0 1 0 12 16 A4 4 0 1 0 12 8 Z M12 2 V5 M12 19 V22 M2 12 H5 M19 12 H22 M4.9 4.9 L7 7 M17 17 L19.1 19.1 M19.1 4.9 L17 7 M7 17 L4.9 19.1',
  book: 'M12 6 C10 4 6 4 4 5 V20 C6 19 10 19 12 20 C14 19 18 19 20 20 V5 C18 4 14 4 12 6 Z M12 6 V20',
  file: 'M6 3 H14 L19 8 V21 H6 Z M14 3 V8 H19',
  lock: 'M6 11 H18 V21 H6 Z M9 11 V7 A3 3 0 0 1 15 7 V11',
  terminal: 'M3 5 H21 V19 H3 Z M7 9 L10 12 L7 15 M12 15 H17',
  play: 'M8 5 L19 12 L8 19 Z',
  pause: 'M8 5 H11 V19 H8 Z M14 5 H17 V19 H14 Z',
  stop: 'M6 6 H18 V18 H6 Z',
  prev: 'M18 5 L8 12 L18 19 Z M6 5 V19',
  next: 'M6 5 L16 12 L6 19 Z M18 5 V19',
  plus: 'M12 5 V19 M5 12 H19',
  trash: 'M5 7 H19 M9 7 V4 H15 V7 M7 7 L8 21 H16 L17 7',
  refresh: 'M20 6 A8 8 0 1 0 21 12 M20 3 V8 H15',
  search: 'M11 4 A7 7 0 1 0 11 18 A7 7 0 1 0 11 4 Z M16 16 L21 21',
  check: 'M4 13 L9 18 L20 6',
  x: 'M6 6 L18 18 M18 6 L6 18',
  upload: 'M12 20 V5 M6 11 L12 5 L18 11',
  download: 'M12 4 V19 M6 13 L12 19 L18 13',
  external: 'M14 4 H20 V10 M20 4 L10 14 M18 14 V20 H4 V6 H10',
  moon: 'M20 14 A8 8 0 0 1 10 4 A9 9 0 1 0 20 14 Z',
  eye: 'M2 12 C6 5 18 5 22 12 C18 19 6 19 2 12 Z M12 9 A3 3 0 1 0 12 15 A3 3 0 1 0 12 9 Z',
  speaker: 'M4 9 H8 L13 5 V19 L8 15 H4 Z M17 9 A5 5 0 0 1 17 15',
  pin: 'M9 3 H15 L14 9 L18 13 H6 L10 9 Z M12 13 V21',
  clock: 'M12 3 A9 9 0 1 0 12 21 A9 9 0 1 0 12 3 Z M12 7 V12 L16 14',
  folder: 'M3 6 H10 L12 8 H21 V20 H3 Z',
  link: 'M9 15 L15 9 M8 7 A4 4 0 0 1 14 7 L16 9 M10 17 A4 4 0 0 1 4 17 L2 15',
  arrowUp: 'M12 20 V4 M6 10 L12 4 L18 10',
  arrowDown: 'M12 4 V20 M6 14 L12 20 L18 14',
  more: 'M5 12 H5.1 M12 12 H12.1 M19 12 H19.1',
};

const SVG_NS = 'http://www.w3.org/2000/svg';

export function icon(name, size = 18) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'square');
  svg.setAttribute('stroke-linejoin', 'miter');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.more);
  svg.append(path);
  return svg;
}

// Yumi's own 7x7 pixel glyphs, as supplied by the buttons API.
export function pixelIcon(patterns, name, size = 18) {
  const pattern = patterns && patterns[name];
  if (!pattern) {
    return el('span', { class: 'glyph-fallback', text: (name || '?').slice(0, 2) });
  }
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 7 7');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('aria-hidden', 'true');
  pattern.forEach((row, y) => {
    [...row].forEach((cell, x) => {
      if (cell !== '#') return;
      const rect = document.createElementNS(SVG_NS, 'rect');
      rect.setAttribute('x', x);
      rect.setAttribute('y', y);
      rect.setAttribute('width', 1);
      rect.setAttribute('height', 1);
      rect.setAttribute('fill', 'currentColor');
      svg.append(rect);
    });
  });
  return svg;
}

// ---------------------------------------------------------------- formatting
export function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  const ru = UI.lang === 'ru';
  if (n < 1024) return `${n}${ru ? ' Б' : ' B'}`;
  const units = ru ? ['КБ', 'МБ', 'ГБ', 'ТБ'] : ['KB', 'MB', 'GB', 'TB'];
  let value = n;
  let unit = -1;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function fmtTime(seconds) {
  const n = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function fmtUptime(seconds) {
  const n = Math.max(0, Math.floor(Number(seconds) || 0));
  const d = Math.floor(n / 86400);
  const h = Math.floor((n % 86400) / 3600);
  const m = Math.floor((n % 3600) / 60);
  const ru = UI.lang === 'ru';
  if (d) return ru ? `${d} д ${h} ч` : `${d}d ${h}h`;
  if (h) return ru ? `${h} ч ${m} мин` : `${h}h ${m}m`;
  return ru ? `${m} мин` : `${m}m`;
}

export function fmtDate(ts) {
  if (!ts) return '—';
  const date = new Date(Number(ts) * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(UI.lang === 'ru' ? 'ru-RU' : 'en-GB', {
    day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function fmtClock(ts) {
  if (!ts) return '—';
  const date = new Date(Number(ts) * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString(UI.lang === 'ru' ? 'ru-RU' : 'en-GB',
    { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function ago(ts) {
  if (!ts) return '';
  const secs = Math.max(0, Date.now() / 1000 - Number(ts));
  const ru = UI.lang === 'ru';
  if (secs < 60) return ru ? 'только что' : 'just now';
  if (secs < 3600) return ru ? `${Math.floor(secs / 60)} мин назад` : `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return ru ? `${Math.floor(secs / 3600)} ч назад` : `${Math.floor(secs / 3600)}h ago`;
  return ru ? `${Math.floor(secs / 86400)} д назад` : `${Math.floor(secs / 86400)}d ago`;
}

// ---------------------------------------------------------------- tiny ui
export function toast(message, kind = '') {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = el('div', { class: `toast ${kind}`.trim(), text: String(message) });
  host.append(node);
  while (host.children.length > 4) host.firstElementChild.remove();
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    node.style.opacity = '0';
    window.setTimeout(() => node.remove(), 220);
  }, 4200);
}

export function errToast(err) {
  toast(err && err.message ? err.message : String(err), 'bad');
}

export function sectionTitle(text, extra) {
  return el('div', { class: 'section-title' }, el('span', { text: tr(text) }), extra || null);
}

export function emptyBox(text) {
  return el('div', { class: 'empty', text: tr(text) });
}

export function badge(text, kind = '') {
  return el('span', { class: `badge ${kind}`.trim(), text: String(text) });
}

export function kv(pairs) {
  const dl = el('dl', { class: 'kv' });
  for (const [key, value] of pairs) {
    dl.append(el('dt', { text: key }));
    dl.append(el('dd', {}, value && value.nodeType ? value : String(value ?? '—')));
  }
  return dl;
}

export function switchControl({ checked, onChange, label }) {
  const button = el('button', {
    type: 'button',
    class: 'switch',
    role: 'switch',
    'aria-checked': String(Boolean(checked)),
    'aria-label': label || (UI.lang === 'ru' ? 'переключатель' : 'switch'),
  });
  button.addEventListener('click', () => {
    const next = button.getAttribute('aria-checked') !== 'true';
    button.setAttribute('aria-checked', String(next));
    onChange(next);
  });
  return button;
}

export function switchRow({ title, hint, checked, onChange }) {
  const control = switchControl({ checked, onChange, label: tr(title) });
  return el('div', { class: 'switch-row' },
    el('div', { class: 'switch-text' },
      el('b', { text: tr(title) }),
      hint ? el('small', { text: tr(hint) }) : null),
    control);
}

export function button(label, { onClick, kind = '', iconName, title, small = false, disabled = false } = {}) {
  const cls = ['btn', kind, small ? 'btn-small' : ''].filter(Boolean).join(' ');
  const node = el('button', { type: 'button', class: cls, title: title || tr(label), disabled },);
  if (iconName) node.append(icon(iconName, small ? 12 : 15));
  if (label) node.append(el('span', { text: tr(label) }));
  if (onClick) node.addEventListener('click', onClick);
  return node;
}

export function field(label, control, hint) {
  return el('label', { class: 'field' },
    el('span', { text: tr(label) }),
    control,
    hint ? el('small', { class: 'card-hint', text: tr(hint) }) : null);
}

// ---------------------------------------------------------------- modal
let closeCurrent = null;

export function modal({ title, body, actions = [], wide = false, onClose }) {
  const back = document.getElementById('modal');
  const box = document.getElementById('modal-box');
  if (!back || !box) return null;
  clear(box);
  box.className = `modal-box pixel-frame${wide ? ' wide' : ''}`;
  const close = () => {
    back.hidden = true;
    clear(box);
    document.removeEventListener('keydown', onKey);
    closeCurrent = null;
    if (onClose) onClose();
  };
  const onKey = (ev) => {
    if (ev.key === 'Escape') close();
  };
  closeCurrent = close;
  box.append(el('h2', { text: tr(title) }));
  if (body) box.append(body);
  const row = el('div', { class: 'modal-actions' });
  for (const action of actions) {
    if (!action) continue;
    row.append(button(action.label, {
      kind: action.kind || '',
      onClick: async () => {
        if (action.onClick) {
          const keep = await action.onClick(close);
          if (keep === false) return;
        }
        close();
      },
    }));
  }
  if (row.children.length) box.append(row);
  back.hidden = false;
  document.addEventListener('keydown', onKey);
  back.onclick = (ev) => {
    if (ev.target === back) close();
  };
  const first = box.querySelector('input, textarea, select, button');
  if (first) first.focus();
  return { close, box };
}

export function confirmDialog({ title, message, confirmLabel, danger = false }) {
  const ru = UI.lang === 'ru';
  title = title || (ru ? 'Точно?' : 'Are you sure?');
  confirmLabel = confirmLabel || (ru ? 'Да' : 'Yes');
  return new Promise((resolve) => {
    let answer = false;
    modal({
      title,
      body: el('p', { class: 'pagelead', text: tr(message) }),
      actions: [
        { label: ru ? 'Отмена' : 'Cancel', onClick: () => { answer = false; } },
        { label: confirmLabel, kind: danger ? 'btn-danger' : 'btn-primary', onClick: () => { answer = true; } },
      ],
      onClose: () => resolve(answer),
    });
  });
}

export function promptDialog({ title, label, value = '', placeholder = '', confirmLabel }) {
  const ru = UI.lang === 'ru';
  confirmLabel = confirmLabel || (ru ? 'ОК' : 'OK');
  return new Promise((resolve) => {
    let answer = null;
    const input = el('input', { type: 'text', value, placeholder, spellcheck: 'false' });
    const wrap = el('div', { class: 'confirm-input' }, field(label || title, input));
    modal({
      title,
      body: wrap,
      actions: [
        { label: ru ? 'Отмена' : 'Cancel', onClick: () => { answer = null; } },
        { label: confirmLabel, kind: 'btn-primary', onClick: () => { answer = input.value; } },
      ],
      onClose: () => resolve(answer),
    });
    window.setTimeout(() => input.focus(), 30);
  });
}

// ---------------------------------------------------------------- setting controls
export function buildSetting(entry, value, onChange, { className = '' } = {}) {
  const control = el('div', { class: 'control' });
  const title = tr(entry.title) || entry.key;
  const hint = tr(entry.hint);
  let inner = null;

  if (entry.type === 'switch') {
    inner = switchControl({ checked: Boolean(value), onChange: (v) => onChange(v), label: title });
  } else if (entry.type === 'select') {
    const select = el('select', {});
    const options = entry.choices && entry.choices.length ? entry.choices : [value];
    for (const choice of options) {
      const label = choice === null
        ? (UI.lang === 'ru' ? 'авто' : 'auto')
        : tr(entry.choice_labels && entry.choice_labels[String(choice)]) || String(choice);
      select.append(el('option', {
        value: choice === null ? '__null__' : String(choice),
        text: label,
        selected: String(choice) === String(value) || (choice === null && value == null),
      }));
    }
    const parse = (raw) => {
      if (raw === '__null__') return null;
      const found = (entry.choices || []).find((c) => String(c) === raw);
      if (found !== undefined) return found;
      if (raw === 'true') return true;
      if (raw === 'false') return false;
      if (raw !== '' && !Number.isNaN(Number(raw)) && entry.default !== undefined
          && typeof entry.default === 'number') return Number(raw);
      return raw;
    };
    select.addEventListener('change', () => onChange(parse(select.value)));
    inner = select;
  } else if (entry.type === 'range') {
    const out = el('output', { class: 'mono', text: String(value ?? entry.default ?? '') });
    const input = el('input', {
      type: 'range',
      min: entry.min ?? 0,
      max: entry.max ?? 100,
      step: entry.step ?? 1,
      value: value ?? entry.default ?? 0,
    });
    input.addEventListener('input', () => { out.textContent = input.value; });
    input.addEventListener('change', () => onChange(Number(input.value)));
    inner = el('div', { class: 'field-row' }, input, out);
  } else if (entry.type === 'number') {
    const input = el('input', {
      type: 'number',
      min: entry.min ?? undefined,
      max: entry.max ?? undefined,
      step: entry.step ?? 1,
      value: value ?? entry.default ?? 0,
    });
    input.addEventListener('change', () => onChange(Number(input.value)));
    inner = input;
  } else if (entry.type === 'list') {
    const valueText = Array.isArray(value) ? JSON.stringify(value) : String(value ?? '');
    const input = el('input', {
      type: 'text',
      value: valueText,
      placeholder: UI.lang === 'ru' ? '["a", "b"] или a, b' : '["a", "b"] or a, b',
      spellcheck: 'false',
    });
    input.addEventListener('change', () => {
      const raw = input.value.trim();
      if (raw.startsWith('[')) {
        try {
          const parsed = JSON.parse(raw);
          if (!Array.isArray(parsed)) throw new Error('not a list');
          onChange(parsed.map((v) => String(v)));
          return;
        } catch {
          toast(UI.lang === 'ru' ? 'Это не JSON-массив' : 'Not a JSON array', 'bad');
          return;
        }
      }
      onChange(raw.split(',').map((v) => v.trim()).filter(Boolean));
    });
    inner = input;
  } else {
    const isSecret = Boolean(entry.secret);
    const long = !isSecret && typeof value === 'string' && (value.length > 90 || value.includes('\n'));
    const masked = isSecret && /^•+$/.test(String(value ?? ''));
    const input = long
      ? el('textarea', { value: masked ? '' : (value ?? entry.default ?? ''), spellcheck: 'false' })
      : el('input', {
        type: isSecret ? 'password' : (entry.type === 'path' ? 'text' : 'text'),
        value: masked ? '' : (value ?? entry.default ?? ''),
        placeholder: masked ? (UI.lang === 'ru' ? 'задан — введи новый, чтобы сменить' : 'set — type a new one to change')
          : (entry.type === 'path' ? (UI.lang === 'ru' ? 'путь к файлу (пусто = её звук)' : 'file path (empty = her chime)') : ''),
        spellcheck: 'false',
        autocomplete: isSecret ? 'new-password' : 'off',
      });
    input.addEventListener('change', () => {
      if (isSecret && input.value === '') return; // blank secret leaves it untouched
      onChange(input.value);
    });
    if (long) {
      input.addEventListener('blur', () => onChange(input.value));
    }
    inner = input;
  }

  control.append(inner);
  const notes = [];
  if (entry.secret) notes.push(badge(UI.lang === 'ru' ? 'секрет' : 'secret', 'amber'));
  if (entry.restart) notes.push(badge(UI.lang === 'ru' ? 'нужен перезапуск' : 'restart', 'red'));
  if (notes.length) control.append(el('div', { class: 'row' }, ...notes));

  return el('div', {
    class: `setting ${className}`.trim(),
    dataset: { key: entry.key },
  },
  el('div', { class: 'setting-text' },
    el('h4', { text: title }),
    hint ? el('small', { text: hint }) : null,
    el('small', { class: 'dim', text: entry.key })),
  control);
}

// ---------------------------------------------------------------- misc
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(UI.lang === 'ru' ? 'Скопировано' : 'Copied', 'good');
    return true;
  } catch {
    try {
      const area = el('textarea', { value: String(text), style: { position: 'fixed', opacity: '0' } });
      document.body.append(area);
      area.select();
      document.execCommand('copy');
      area.remove();
      toast(UI.lang === 'ru' ? 'Скопировано' : 'Copied', 'good');
      return true;
    } catch {
      toast(UI.lang === 'ru' ? 'Браузер не дал скопировать' : 'Could not copy', 'bad');
      return false;
    }
  }
}

export function spinner(text) {
  return el('div', { class: 'loading pixel-frame',
    text: tr(text || (UI.lang === 'ru' ? 'Юми думает…' : 'Yumi is thinking…')) });
}

export function closeModal() {
  if (closeCurrent) closeCurrent();
}
