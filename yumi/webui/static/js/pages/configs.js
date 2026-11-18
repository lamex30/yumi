import { api } from '../api.js';
import {
  el, clear, tr, icon, fmtBytes, fmtDate, errToast, toast, confirmDialog,
  sectionTitle, emptyBox, badge,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let files = [];
  let current = null;
  let original = '';
  let dirty = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Конфиги' : 'Config files' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'JSON-файлы, на которых держится Юми. Перед каждой записью рядом с оригиналом кладётся резервная копия.'
      : 'The JSON files Yumi stands on. Every save leaves a backup beside the original.' }),
  );

  const grid = el('div', { class: 'grid auto' });
  view.append(grid);

  const editorTitle = el('h3', { text: lang() === 'ru' ? 'Редактор' : 'Editor' });
  const editorPath = el('code', { class: 'card-hint', text: '' });
  const editorBadge = el('span', { class: 'badge', text: '' });
  const textarea = el('textarea', {
    class: 'editor', spellcheck: 'false', autocomplete: 'off', wrap: 'off',
  });
  const editorNote = el('div', { class: 'card-hint', text: '' });
  const saveButton = el('button', {
    class: 'btn btn-primary btn-small', type: 'button', text: lang() === 'ru' ? 'Сохранить' : 'Save',
    disabled: true,
  });
  const formatButton = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Выровнять' : 'Format',
    onclick: () => {
      try {
        textarea.value = JSON.stringify(JSON.parse(textarea.value), null, 2);
        markDirty(true);
      } catch (err) {
        toast(`${lang() === 'ru' ? 'Это не JSON' : 'Not JSON'}: ${err.message}`, 'bad');
      }
    },
  });
  const reloadButton = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Перечитать' : 'Reload',
    onclick: async () => {
      if (dirty && !await confirmDialog({
        title: lang() === 'ru' ? 'Потерять правки?' : 'Lose the edits?',
        message: lang() === 'ru' ? 'Файл будет прочитан заново.' : 'The file will be read again.',
        confirmLabel: lang() === 'ru' ? 'Перечитать' : 'Reload', danger: true,
      })) return;
      if (current) open(current);
    },
  });
  const closeButton = el('button', {
    class: 'btn btn-small btn-ghost', type: 'button', text: lang() === 'ru' ? 'Закрыть' : 'Close',
    onclick: () => closeEditor(),
  });
  const editorCard = el('div', { class: 'card', style: { marginTop: '16px' }, hidden: true },
    el('div', { class: 'row between' },
      el('div', {}, editorTitle, editorPath), editorBadge),
    el('div', { style: { margin: '12px 0' } }, textarea),
    el('div', { class: 'row' },
      saveButton, formatButton, reloadButton, closeButton,
      el('span', { class: 'spacer' }), editorNote));
  view.append(editorCard);

  textarea.addEventListener('input', () => markDirty(textarea.value !== original));
  textarea.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') {
      ev.preventDefault();
      save();
    }
    if (ev.key === 'Tab') {
      ev.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = `${textarea.value.slice(0, start)}  ${textarea.value.slice(end)}`;
      textarea.selectionStart = textarea.selectionEnd = start + 2;
      markDirty(true);
    }
  });
  saveButton.addEventListener('click', save);

  function markDirty(value) {
    dirty = value;
    saveButton.disabled = !dirty || !current?.writable;
    editorBadge.textContent = dirty
      ? (lang() === 'ru' ? 'не сохранено' : 'unsaved')
      : (lang() === 'ru' ? 'сохранено' : 'saved');
    editorBadge.className = dirty ? 'badge amber' : 'badge mint';
  }

  async function loadFiles() {
    try {
      const data = await api.get('/api/configs');
      files = Array.isArray(data.files) ? data.files : [];
      drawFiles();
    } catch (err) {
      grid.replaceChildren(emptyBox(err.message));
    }
  }

  function drawFiles() {
    clear(grid);
    if (!files.length) {
      grid.append(emptyBox(lang() === 'ru'
        ? 'Файлов конфигурации пока нет: Юми создаст их при первом сохранении.'
        : 'No config files yet: Yumi creates them on the first save.'));
      return;
    }
    for (const file of files) {
      const card = el('div', { class: 'card config-card' },
        el('div', { class: 'row between' },
          el('b', { text: tr(file.title) || file.name }),
          file.name === 'webpanel' ? badge('secret', 'amber') : null),
        el('code', { text: file.path }),
        el('div', { class: 'card-hint', text: file.exists
          ? `${fmtBytes(file.size)} · ${fmtDate(file.mtime)}`
          : (lang() === 'ru' ? 'файл ещё не создан' : 'not created yet') }),
        el('div', { class: 'row', style: { marginTop: '6px' } },
          el('button', {
            class: 'btn btn-small', type: 'button',
            text: file.name === 'webpanel'
              ? (lang() === 'ru' ? 'Посмотреть' : 'View')
              : (lang() === 'ru' ? 'Открыть' : 'Open'),
            onclick: () => open(file),
          })));
      grid.append(card);
    }
  }

  async function open(file) {
    if (dirty && !await confirmDialog({
      title: lang() === 'ru' ? 'Потерять правки?' : 'Lose the edits?',
      message: lang() === 'ru' ? 'Несохранённый текст пропадёт.' : 'The unsaved text will go.',
      confirmLabel: lang() === 'ru' ? 'Открыть другой' : 'Open another', danger: true,
    })) return;
    try {
      const data = await api.get(`/api/config/${file.name}`);
      current = { ...file, ...data.file, writable: data.writable };
      original = data.text || '';
      textarea.value = original;
      textarea.readOnly = !data.writable;
      editorCard.hidden = false;
      editorTitle.textContent = tr(data.file.title) || file.name;
      editorPath.textContent = data.file.path;
      editorNote.textContent = data.writable
        ? (lang() === 'ru' ? `Резервные копии: ~/.config/yumi/backups/.` : 'Backups: ~/.config/yumi/backups/.')
        : (lang() === 'ru' ? 'Этот файл только для чтения: токен меняется на странице «Безопасность».' : 'Read-only: change the token on the Security page.');
      markDirty(false);
      editorCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
      textarea.focus();
    } catch (err) {
      errToast(err);
    }
  }

  function closeEditor() {
    closeEditorAsync();
  }

  async function closeEditorAsync() {
    if (dirty && !await confirmDiscard()) return;
    editorCard.hidden = true;
    current = null;
    original = '';
    textarea.value = '';
    dirty = false;
  }

  async function confirmDiscard() {
    return confirmDialog({
      title: lang() === 'ru' ? 'Закрыть без сохранения?' : 'Close without saving?',
      message: lang() === 'ru' ? 'Правки пропадут.' : 'The edits will be lost.',
      confirmLabel: lang() === 'ru' ? 'Закрыть' : 'Close', danger: true,
    });
  }

  async function save() {
    if (!current || !current.writable || !dirty) return;
    let data;
    try {
      data = JSON.parse(textarea.value);
    } catch (err) {
      toast(`${lang() === 'ru' ? 'Это не JSON' : 'Not JSON'}: ${err.message}`, 'bad');
      return;
    }
    if (!(data && typeof data === 'object')) {
      toast(lang() === 'ru' ? 'Нужен объект или список JSON' : 'A JSON object or array is needed', 'bad');
      return;
    }
    saveButton.disabled = true;
    try {
      const result = await api.put(`/api/config/${current.name}`, { data });
      toast(lang() === 'ru' ? 'Сохранено' : 'Saved', 'good');
      original = textarea.value;
      markDirty(false);
      if (result.backup) editorNote.textContent = `${lang() === 'ru' ? 'копия' : 'backup'}: ${result.backup}`;
      await ctx.refreshStatus();
      if (current.name === 'settings' || current.name === 'actions') await loadFiles();
    } catch (err) {
      errToast(err);
    } finally {
      markDirty(textarea.value !== original);
    }
  }

  const beforeUnload = (ev) => {
    if (dirty) {
      ev.preventDefault();
      ev.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', beforeUnload);

  loadFiles();
  return () => {
    window.removeEventListener('beforeunload', beforeUnload);
  };
}
