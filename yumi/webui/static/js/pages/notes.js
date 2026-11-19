import { api } from '../api.js';
import {
  el, clear, fmtDate, errToast, toast, confirmDialog, emptyBox, badge, field, modal,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  const ru = () => lang() === 'ru';
  let items = [];
  let busy = false;

  view.append(
    el('h2', { class: 'pagehead', text: ru() ? 'Заметки' : 'Notes' }),
    el('p', { class: 'pagelead', text: ru()
      ? 'Скажи Юми «запиши заметку …» или напиши здесь. Всё лежит в sqlite с векторами, поэтому ищется и по смыслу.'
      : 'Say «write a note …» to Yumi or type here. Everything lives in sqlite with vectors, so it can be found by meaning too.' }),
  );

  const search = el('input', {
    type: 'search',
    placeholder: ru() ? 'Найти по заметкам…' : 'Search the notes…',
    spellcheck: 'false',
  });
  const resultNote = el('span', { class: 'card-hint', text: '' });
  const searchButton = el('button', {
    class: 'btn btn-primary', type: 'button', text: ru() ? 'Искать' : 'Search',
    onclick: () => load(true),
  });
  const refreshButton = el('button', {
    class: 'btn', type: 'button', text: ru() ? 'Все заметки' : 'All notes',
    onclick: () => { search.value = ''; load(false); },
  });
  const addButton = el('button', {
    class: 'btn btn-primary', type: 'button', text: ru() ? 'Новая заметка' : 'New note',
    onclick: () => openEditor(),
  });
  const voiceButton = el('button', {
    class: 'btn', type: 'button', text: ru() ? 'Записать голосом' : 'Record by voice',
    onclick: () => recordVoice(),
  });
  const clearButton = el('button', {
    class: 'btn btn-danger btn-small', type: 'button', text: ru() ? 'Стереть всё' : 'Erase all',
    onclick: async () => {
      if (!await confirmDialog({
        title: ru() ? 'Стереть все заметки?' : 'Erase every note?',
        message: ru() ? 'Это необратимо.' : 'This cannot be undone.',
        confirmLabel: ru() ? 'Стереть' : 'Erase', danger: true,
      })) return;
      try {
        const data = await api.notesClear();
        toast(`${ru() ? 'Стёрто' : 'Erased'}: ${data.deleted}`, 'good');
        await load(false);
      } catch (err) { errToast(err); }
    },
  });

  const toolbar = el('div', { class: 'row', style: { marginBottom: '14px' } },
    search, searchButton, refreshButton, addButton, voiceButton,
    el('span', { class: 'spacer' }), resultNote, clearButton);
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
    resultNote.textContent = ru() ? 'читаю…' : 'reading…';
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (q) params.set('q', q);
      const data = await api.notesList(params.toString());
      items = Array.isArray(data.items) ? data.items : [];
      resultNote.textContent = q
        ? `${ru() ? 'поиск' : 'search'}: «${q}» · ${items.length}`
        : `${items.length} ${ru() ? 'заметок' : 'notes'}`;
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
      list.append(emptyBox(search.value.trim()
        ? (ru() ? 'Ничего не нашлось.' : 'Nothing was found.')
        : (ru() ? 'Заметок пока нет. Скажи Юми «запиши заметку …».'
          : 'There are no notes yet. Tell Yumi «write a note …».')));
      return;
    }
    for (const note of items) {
      const head = el('div', { class: 'meta' },
        el('span', { text: fmtDate(note.updated || note.ts) }),
        badge(note.source === 'voice' ? (ru() ? 'голос' : 'voice') : (ru() ? 'вручную' : 'typed'),
          note.source === 'voice' ? 'amber' : ''));
      const actions = el('div', { class: 'row', style: { marginTop: '8px' } },
        el('button', {
          class: 'btn btn-small', type: 'button', text: ru() ? 'Озвучить' : 'Speak',
          onclick: async () => {
            const said = [note.title, note.text].filter(Boolean).join('. ');
            try {
              await api.post('/api/voice/speak', { text: said.slice(0, 1200) });
              toast(ru() ? 'Говорю' : 'Speaking', 'good');
            } catch (err) { errToast(err); }
          },
        }),
        el('button', {
          class: 'btn btn-small', type: 'button', text: ru() ? 'Изменить' : 'Edit',
          onclick: () => openEditor(note),
        }),
        el('button', {
          class: 'btn btn-small btn-danger', type: 'button', text: ru() ? 'Удалить' : 'Delete',
          onclick: async () => {
            if (!await confirmDialog({
              title: ru() ? 'Удалить заметку?' : 'Delete the note?',
              message: note.title,
              confirmLabel: ru() ? 'Удалить' : 'Delete', danger: true,
            })) return;
            try {
              await api.noteDelete(note.id);
              toast(ru() ? 'Удалено' : 'Deleted', 'good');
              await load(search.value.trim() !== '');
            } catch (err) { errToast(err); }
          },
        }));
      list.append(el('div', { class: 'note-item' },
        el('div', { class: 'note-title', text: note.title || '—' }),
        head,
        el('div', { class: 'note-text', text: note.text }),
        actions));
    }
  }

  function openEditor(note = null, preset = '') {
    const title = el('input', {
      type: 'text', spellcheck: 'false',
      placeholder: ru() ? 'Заголовок (можно пусто)' : 'Title (optional)',
      value: note?.title || '',
    });
    const text = el('textarea', {
      rows: 8,
      placeholder: ru() ? 'Текст заметки…' : 'The note itself…',
      spellcheck: 'false',
    });
    text.value = note?.text || preset || '';
    modal({
      title: note ? (ru() ? 'Изменить заметку' : 'Edit the note') : (ru() ? 'Новая заметка' : 'New note'),
      wide: true,
      body: el('div', { class: 'stack' },
        field(ru() ? 'Заголовок' : 'Title', title),
        field(ru() ? 'Текст' : 'Text', text)),
      actions: [
        { label: ru() ? 'Отмена' : 'Cancel' },
        {
          label: ru() ? 'Сохранить' : 'Save', kind: 'btn-primary',
          onClick: async () => {
            const body = text.value.trim();
            if (!body) {
              toast(ru() ? 'Пустая заметка не сохранится' : 'An empty note is not saved', 'warn');
              text.focus();
              return false;
            }
            try {
              if (note) await api.noteUpdate(note.id, body, title.value.trim());
              else await api.noteAdd(body, title.value.trim());
              toast(ru() ? 'Сохранено' : 'Saved', 'good');
              await load(search.value.trim() !== '');
            } catch (err) {
              errToast(err);
              return false;
            }
          },
        },
      ],
    });
  }

  async function recordVoice() {
    voiceButton.disabled = true;
    const was = voiceButton.textContent;
    voiceButton.textContent = ru() ? 'слушаю…' : 'listening…';
    try {
      const data = await api.post('/api/voice/listen', { seconds: 10 });
      const heard = String(data.heard || '').trim();
      if (!heard) {
        toast(ru() ? 'Тишина. Попробуй ещё раз.' : 'Silence. Try again.', 'warn');
        return;
      }
      openEditor(null, heard);
      if (ru()) toast('Проверь текст и нажми «Сохранить»', 'good');
      else toast('Check the text and press Save', 'good');
    } catch (err) {
      errToast(err);
    } finally {
      voiceButton.disabled = false;
      voiceButton.textContent = was;
    }
  }

  const offNotes = ctx.onEvent('notes', () => load(search.value.trim() !== ''));
  load(false);

  return () => { offNotes(); };
}
