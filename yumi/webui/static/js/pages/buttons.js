import { api } from '../api.js';
import {
  el, tr, icon, pixelIcon, errToast, toast, sectionTitle, emptyBox, modal, field,
  confirmDialog, badge,
} from '../ui.js';

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let data = { actions: [], ring: [], ring_order: [], ring_off: [], icons: [], icon_patterns: {} };

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Кнопки' : 'Buttons' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Кольцо вокруг Юми и твои собственные команды. Порядок и выключенные кнопки сохраняются в её настройках.'
      : 'The ring around Yumi and your own commands. Order and hidden buttons live in her settings.' }),
  );

  // a command with requires_confirmation waits for a word or for a click here
  const confirmBox = el('div', { class: 'card', style: { marginBottom: '16px' } });
  view.append(confirmBox);

  function drawConfirm() {
    const nlu = ctx.status?.runtime?.nlu || {};
    if (!nlu.confirm) {
      confirmBox.replaceChildren();
      confirmBox.hidden = true;
      return;
    }
    confirmBox.hidden = false;
    const answer = async (ok) => {
      try {
        await api.nluConfirm(ok);
        toast(ok ? (lang() === 'ru' ? 'Запустила' : 'Ran')
          : (lang() === 'ru' ? 'Отменила' : 'Cancelled'), 'good');
        await ctx.refreshStatus();
      } catch (err) { errToast(err); }
    };
    confirmBox.replaceChildren(
      el('h3', { text: lang() === 'ru' ? 'Подтверди команду' : 'Confirm the command' }),
      el('p', { class: 'card-hint', text: nlu.last_intent || '' }),
      el('div', { class: 'row' },
        el('button', {
          class: 'btn btn-small btn-primary', type: 'button',
          text: lang() === 'ru' ? 'Запустить' : 'Run',
          onclick: () => answer(true),
        }),
        el('button', {
          class: 'btn btn-small', type: 'button',
          text: lang() === 'ru' ? 'Отменить' : 'Cancel',
          onclick: () => answer(false),
        })));
  }

  const offStatus = ctx.onStatus ? ctx.onStatus(drawConfirm) : null;
  drawConfirm();

  const ringBox = el('div', { class: 'ring-list' });
  const ringCard = el('div', { class: 'card' },
    el('div', { class: 'row between' },
      el('h3', { text: lang() === 'ru' ? 'Кольцо' : 'The ring' }),
      el('span', { class: 'card-hint', text: lang() === 'ru' ? 'стрелки меняют порядок' : 'arrows change the order' })),
    ringBox);
  view.append(ringCard);

  const actionsBox = el('div', { class: 'grid auto', style: { marginTop: '16px' } });
  const addButton = el('button', {
    class: 'btn btn-primary', type: 'button',
    onclick: () => editAction(null),
  }, icon('plus', 14), el('span', { text: lang() === 'ru' ? 'Своя кнопка' : 'New button' }));
  view.append(sectionTitle(lang() === 'ru' ? 'Свои команды' : 'Your commands', addButton));
  view.append(actionsBox);

  async function load() {
    try {
      data = await api.get('/api/actions');
      data.actions = data.actions || [];
      data.ring = data.ring || [];
      data.icon_patterns = data.icon_patterns || {};
      data.ring_off = data.ring_off || [];
      drawRing();
      drawActions();
    } catch (err) {
      ringBox.replaceChildren(emptyBox(err.message));
      actionsBox.replaceChildren(emptyBox(err.message));
    }
  }

  function drawRing() {
    ringBox.replaceChildren();
    if (!data.ring.length) {
      ringBox.append(emptyBox(lang() === 'ru' ? 'Кольцо пустое' : 'The ring is empty'));
      return;
    }
    const ids = data.ring.map((item) => item.id);
    data.ring.forEach((item, index) => {
      const active = !item.off;
      const row = el('div', {
        class: `ring-item ${active ? '' : 'off'}`.trim(),
        title: item.off ? (lang() === 'ru' ? 'выключена' : 'hidden') : '',
      },
      el('div', { class: 'order', text: String(index + 1).padStart(2, '0') }),
      el('div', { class: 'glyph' }, pixelIcon(data.icon_patterns, item.icon, 20)),
      el('div', {},
        el('b', { text: item.label || item.id }),
        el('small', { text: item.custom ? (lang() === 'ru' ? 'своя' : 'custom') : item.id })),
      el('div', { class: 'arrows' },
        el('button', {
          class: 'btn btn-small btn-icon', type: 'button', title: lang() === 'ru' ? 'выше' : 'up',
          disabled: index === 0,
          onclick: () => move(index, -1),
        }, icon('arrowUp', 12)),
        el('button', {
          class: 'btn btn-small btn-icon', type: 'button', title: lang() === 'ru' ? 'ниже' : 'down',
          disabled: index === ids.length - 1,
          onclick: () => move(index, 1),
        }, icon('arrowDown', 12)),
        item.fixed || item.custom ? null : el('button', {
          class: `btn btn-small ${active ? '' : 'btn-on'}`, type: 'button',
          text: active ? (lang() === 'ru' ? 'скрыть' : 'hide') : (lang() === 'ru' ? 'вернуть' : 'show'),
          onclick: () => toggleOff(item.id),
        })));
      ringBox.append(row);
    });
  }

  async function move(index, delta) {
    const ids = data.ring.map((item) => item.id);
    const to = index + delta;
    if (to < 0 || to >= ids.length) return;
    [ids[index], ids[to]] = [ids[to], ids[index]];
    const result = await ctx.applySettings({ ring: ids });
    if (!result) return;
    data.ring_order = ids;
    const byId = new Map(data.ring.map((item) => [item.id, item]));
    data.ring = ids.map((id) => byId.get(id)).filter(Boolean);
    drawRing();
  }

  async function toggleOff(id) {
    const off = new Set(data.ring_off || []);
    if (off.has(id)) off.delete(id);
    else off.add(id);
    const list = [...off];
    const result = await ctx.applySettings({ ring_off: list });
    if (!result) return;
    data.ring_off = list;
    for (const item of data.ring) {
      if (item.id === id) item.off = off.has(id);
    }
    drawRing();
  }

  function drawActions() {
    actionsBox.replaceChildren();
    if (!data.actions.length) {
      actionsBox.append(emptyBox(lang() === 'ru'
        ? 'Пока нет своих кнопок. Добавь команду — она появится в кольце.'
        : 'No commands yet. Add one — it appears in her ring.'));
      return;
    }
    data.actions.forEach((action, index) => {
      const card = el('div', { class: 'card action-card' },
        el('div', { class: 'row between' },
          el('div', { class: 'row' },
            el('span', { class: 'glyph', style: { width: '30px', height: '30px', display: 'grid', placeItems: 'center', border: '2px solid var(--line)', background: '#15111f', color: 'var(--accent)' } },
              pixelIcon(data.icon_patterns, action.icon, 18)),
            el('b', { text: lang() === 'ru' ? (action.label_ru || action.label) : action.label })),
          data.ring_off?.includes(`cmd:${action.command}`) ? badge('off', 'red') : null),
        el('div', { class: 'cmd', text: action.command, title: action.command }),
        action.url ? el('div', { class: 'card-hint', text: `URL: ${action.url}` }) : null,
        (action.id || action.priority || action.requires_confirmation
          || Object.keys(action.entities || {}).length)
          ? el('div', {
            class: 'card-hint',
            text: [
              action.id ? `id: ${action.id}` : '',
              action.priority ? `priority: ${action.priority}` : '',
              action.requires_confirmation
                ? (lang() === 'ru' ? 'спрашивает перед запуском' : 'asks first') : '',
              Object.keys(action.entities || {}).length
                ? `entities: ${Object.keys(action.entities).join(', ')}` : '',
            ].filter(Boolean).join(' · '),
          })
          : null,
        el('div', { class: 'row', style: { marginTop: 'auto', paddingTop: '8px' } },
          el('button', {
            class: 'btn btn-small btn-primary', type: 'button', text: lang() === 'ru' ? 'Запустить' : 'Run',
            onclick: async () => {
              try {
                await api.post(`/api/actions/${index}/run`);
                toast(lang() === 'ru' ? 'Запустила' : 'Ran', 'good');
              } catch (err) { errToast(err); }
            },
          }),
          el('button', {
            class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Править' : 'Edit',
            onclick: () => editAction(index),
          }),
          el('button', {
            class: 'btn btn-small btn-danger', type: 'button', text: lang() === 'ru' ? 'Удалить' : 'Delete',
            onclick: () => removeAction(index),
          })));
      actionsBox.append(card);
    });
  }

  function editAction(index) {
    const original = index == null ? null : data.actions[index];
    const draft = original ? JSON.parse(JSON.stringify(original)) : {
      label: '', label_ru: '', icon: 'star', command: '', phrases: [], keywords: [], speak: '',
      url: '', id: '', priority: 0, requires_confirmation: false, entities: {},
    };
    if (!draft.label_ru) draft.label_ru = draft.label || '';
    let chosenIcon = draft.icon || 'star';

    const iconGrid = el('div', { class: 'icon-grid' });
    const drawIcons = () => {
      iconGrid.replaceChildren();
      for (const name of data.icons || []) {
        const pick = el('button', {
          class: `icon-pick ${name === chosenIcon ? 'on' : ''}`.trim(),
          type: 'button', title: name,
          onclick: () => { chosenIcon = name; drawIcons(); },
        }, pixelIcon(data.icon_patterns, name, 20));
        iconGrid.append(pick);
      }
    };
    drawIcons();

    const label = el('input', { type: 'text', value: draft.label || '', maxlength: 18 });
    const labelRu = el('input', { type: 'text', value: draft.label_ru || '', maxlength: 18 });
    const command = el('input', { type: 'text', value: draft.command || '', spellcheck: 'false', placeholder: 'foot --title=…' });
    const url = el('input', { type: 'text', value: draft.url || '', spellcheck: 'false', placeholder: 'https://… ({query} = words)' });
    const phrases = el('input', { type: 'text', value: (draft.phrases || []).join(', '), placeholder: lang() === 'ru' ? 'скажи «…»' : 'say “…”' });
    const keywords = el('input', { type: 'text', value: (draft.keywords || []).join(', '), placeholder: lang() === 'ru' ? 'ключевые слова' : 'keywords' });
    const speak = el('input', { type: 'text', value: draft.speak || '', placeholder: lang() === 'ru' ? 'что сказать после запуска' : 'what to say afterwards' });
    const id = el('input', { type: 'text', value: draft.id || '', maxlength: 64, spellcheck: 'false', placeholder: 'open_browser' });
    const priority = el('input', { type: 'number', value: String(draft.priority ?? 0), min: '-100', max: '100', step: '1' });
    const confirmFirst = el('input', { type: 'checkbox', checked: Boolean(draft.requires_confirmation) });
    const entities = el('input', {
      type: 'text', value: Object.keys(draft.entities || {}).join(', '),
      placeholder: 'volume, time, date', spellcheck: 'false',
    });

    const body = el('div', { class: 'stack' },
      el('div', { class: 'form-grid' },
        field(lang() === 'ru' ? 'Название' : 'Name', label),
        field(lang() === 'ru' ? 'Название по-русски' : 'Russian name', labelRu),
        el('div', { class: 'full' }, field(lang() === 'ru' ? 'Команда' : 'Command', command)),
        el('div', { class: 'full' }, field('URL', url)),
        el('div', { class: 'full' }, field(lang() === 'ru' ? 'Фразы' : 'Phrases', phrases)),
        el('div', { class: 'full' }, field(lang() === 'ru' ? 'Ключевые слова' : 'Keywords', keywords)),
        el('div', { class: 'full' }, field(lang() === 'ru' ? 'Реплика' : 'Say', speak)),
        field(lang() === 'ru' ? 'ID команды' : 'Command id', id),
        field(lang() === 'ru' ? 'Приоритет' : 'Priority', priority),
        field(lang() === 'ru' ? 'Сущности' : 'Entities', entities),
        el('div', { class: 'full' }, el('label', { class: 'row' },
          confirmFirst,
          el('span', { text: lang() === 'ru' ? 'Спрашивать перед запуском' : 'Ask before running' })))),
      el('div', { class: 'field' }, el('span', { text: lang() === 'ru' ? 'Иконка' : 'Icon' }), iconGrid));

    modal({
      title: original ? (lang() === 'ru' ? 'Править кнопку' : 'Edit button') : (lang() === 'ru' ? 'Новая кнопка' : 'New button'),
      body,
      wide: true,
      actions: [
        { label: lang() === 'ru' ? 'Отмена' : 'Cancel' },
        {
          label: lang() === 'ru' ? 'Сохранить' : 'Save',
          kind: 'btn-primary',
          onClick: async () => {
            if (!command.value.trim()) {
              toast(lang() === 'ru' ? 'Нужна команда' : 'A command is needed', 'bad');
              return false;
            }
            const next = data.actions.map((a) => ({ ...a }));
            const entry = {
              ...draft,
              label: (label.value.trim() || command.value.trim() || url.value.trim()).slice(0, 18),
              label_ru: (labelRu.value.trim() || label.value.trim() || command.value.trim() || url.value.trim()).slice(0, 18),
              icon: chosenIcon,
              command: command.value.trim(),
              url: url.value.trim(),
              speak: speak.value.trim(),
              phrases: phrases.value.split(',').map((v) => v.trim()).filter(Boolean),
              keywords: keywords.value.split(',').map((v) => v.trim()).filter(Boolean),
              id: id.value.trim(),
              priority: Number.parseInt(priority.value, 10) || 0,
              requires_confirmation: confirmFirst.checked,
              entities: entities.value.split(',')
                .map((v) => v.trim().toLowerCase()).filter(Boolean)
                .reduce((acc, key) => {
                  if (['volume', 'time', 'date'].includes(key)) acc[key] = true;
                  return acc;
                }, {}),
            };
            if (index == null) next.push(entry);
            else next[index] = entry;
            try {
              await api.put('/api/actions', { actions: next });
              toast(lang() === 'ru' ? 'Сохранено' : 'Saved', 'good');
              await load();
            } catch (err) { errToast(err); return false; }
            return true;
          },
        },
      ],
    });
  }

  async function removeAction(index) {
    const action = data.actions[index];
    if (!action) return;
    if (!await confirmDialog({
      title: lang() === 'ru' ? 'Удалить кнопку?' : 'Delete the button?',
      message: `${action.label || action.command}`,
      confirmLabel: lang() === 'ru' ? 'Удалить' : 'Delete', danger: true,
    })) return;
    const next = data.actions.filter((_, i) => i !== index);
    try {
      await api.put('/api/actions', { actions: next });
      toast(lang() === 'ru' ? 'Удалена' : 'Deleted', 'good');
      await load();
    } catch (err) { errToast(err); }
  }

  load();
  return () => { if (offStatus) offStatus(); };
}
