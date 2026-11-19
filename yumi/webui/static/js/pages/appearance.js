import { api } from '../api.js';
import {
  el, tr, icon, toast, errToast, buildSetting, sectionTitle, copyText, badge, emptyBox,
} from '../ui.js';

const KEYS = ['recolor', 'hue', 'scale', 'outfit'];

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let status = ctx.status || {};

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Внешний вид' : 'Look' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Цвета от обоев или вручную, наряд, масштаб пикселей и экран, на котором она живёт.'
      : 'Wallpaper colors or by hand, outfit, pixel scale and the screen she lives on.' }),
  );

  // ---------------------------------------------------------------- palette preview
  const swatches = el('div', { class: 'swatches' });
  const paletteNote = el('div', { class: 'card-hint', text: '' });
  const paletteCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Её палитра сейчас' : 'Her palette right now' }),
    swatches, paletteNote,
    el('div', { class: 'row', style: { marginTop: '12px' } },
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Пипетка' : 'Pipette',
        onclick: () => ctx.stateAction('pipette'),
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Пересобрать цвета' : 'Recollect colors',
        onclick: () => ctx.applySettings({ recolor: Boolean(status.settings?.recolor) }),
      }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Другой экран' : 'Next screen',
        onclick: () => ctx.stateAction('monitor'),
      })));

  const history = el('div', { class: 'swatches' });
  const historyCard = el('div', { class: 'card' },
    el('h3', { text: lang() === 'ru' ? 'Из пипетки' : 'Picked by eye' }),
    history,
    el('p', { class: 'card-hint', style: { marginTop: '10px' }, text: lang() === 'ru'
      ? 'Нажми на цвет, чтобы скопировать его код.'
      : 'Click a color to copy its code.' }));

  view.append(el('div', { class: 'grid two' }, paletteCard, historyCard));

  // ---------------------------------------------------------------- settings
  const box = el('div', { class: 'card', style: { marginTop: '16px' } });
  view.append(sectionTitle(lang() === 'ru' ? 'Форма и цвет' : 'Shape and color'));
  view.append(box);

  const resetHue = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Свои цвета' : 'Her own colors',
    title: lang() === 'ru' ? 'Убрать ручной оттенок' : 'Clear the manual hue',
    onclick: async () => {
      const result = await ctx.applySettings({ hue: null });
      if (result) {
        status.settings = { ...(status.settings || {}), hue: null };
        drawSettings();
        toast(lang() === 'ru' ? 'Оттенок снят' : 'Hue cleared', 'good');
      }
    },
  });

  const configButton = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Своя палитра (JSON)' : 'Palette override (JSON)',
    onclick: () => ctx.navigate('configs'),
  });

  function drawSettings() {
    box.replaceChildren();
    const schema = ctx.schema?.settings || [];
    const picked = KEYS.map((key) => schema.find((entry) => entry.key === key)).filter(Boolean);
    if (!picked.length) {
      box.append(emptyBox(lang() === 'ru' ? 'Нет настроек внешнего вида' : 'No look settings'));
      return;
    }
    for (const entry of picked) {
      const value = status.settings?.[entry.key];
      box.append(buildSetting(entry, value, async (next) => {
        const result = await ctx.applySettings({ [entry.key]: next });
        if (result?.result?.saved) {
          status.settings = { ...(status.settings || {}), ...result.result.saved };
          draw();
        }
      }));
    }
    box.append(el('div', { class: 'row', style: { marginTop: '10px' } }, resetHue, configButton));
  }

  function draw() {
    const runtime = status.runtime || {};
    swatches.replaceChildren();
    const colors = runtime.ui_colors || {};
    const entries = Object.entries(colors);
    if (!entries.length) {
      swatches.append(el('span', { class: 'card-hint', text: lang() === 'ru' ? 'палитра ещё не пришла' : 'no palette yet' }));
    }
    for (const [name, color] of entries) {
      const sw = el('button', {
        class: 'swatch', type: 'button', title: `${name} · ${color}`,
        style: { background: color },
        onclick: () => copyText(color),
      });
      swatches.append(sw);
    }
    paletteNote.textContent = runtime.palette && Object.keys(runtime.palette).length
      ? `${lang() === 'ru' ? 'из обоев' : 'from the wallpaper'} · ${runtime.palette.surface || ''}`
      : (lang() === 'ru' ? 'её собственные цвета' : 'her own colors');
    const monitor = runtime.monitors;
    paletteNote.textContent += ` · ${lang() === 'ru' ? 'экранов' : 'screens'}: ${monitor ?? 1}`
      + (status.state?.monitor ? ` (${status.state.monitor})` : '');

    history.replaceChildren();
    const picked = status.state?.colors || [];
    if (!picked.length) {
      history.append(el('span', { class: 'card-hint', text: lang() === 'ru' ? 'пока ничего' : 'nothing yet' }));
    }
    for (const color of picked) {
      history.append(el('button', {
        class: 'swatch', type: 'button', title: color,
        style: { background: color },
        onclick: () => copyText(color),
      }));
    }
  }

  const offStatus = ctx.onStatus((next) => {
    status = next;
    draw();
  });
  status = ctx.status || status;
  drawSettings();
  draw();

  return () => { offStatus(); };
}
