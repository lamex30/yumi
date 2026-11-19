import { api } from '../api.js';
import {
  el, tr, icon, fmtDate, errToast, toast, confirmDialog, sectionTitle, buildSetting,
  badge, kv, copyText, emptyBox,
} from '../ui.js';

const WEB_KEYS = ['web_panel', 'web_panel_host', 'web_panel_port', 'web_panel_open', 'web_panel_session_hours'];

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let status = ctx.status || {};
  let security = null;
  let revealed = '';

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Безопасность' : 'Security' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Панель не открывает порт, пока её не включат. Вход — по случайному токену, сессия — HttpOnly, изменения — с CSRF.'
      : 'The panel opens no port until enabled. Entry is a random token, the session is HttpOnly, mutations need CSRF.' }),
  );

  // ---------------------------------------------------------------- warnings
  const warnings = el('div', { class: 'stack', style: { marginBottom: '16px' } });
  view.append(warnings);

  // ---------------------------------------------------------------- panel state
  const panelInfo = el('div', {});
  const stateCard = el('div', { class: 'card' },
    el('div', { class: 'row between' },
      el('h3', { text: lang() === 'ru' ? 'Панель' : 'The panel' }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Открыть журнал' : 'Open the log',
        onclick: () => ctx.navigate('logs'),
      })),
    panelInfo);
  view.append(stateCard);

  // ---------------------------------------------------------------- access settings
  const access = el('div', { class: 'card', style: { marginTop: '14px' } });
  view.append(sectionTitle(lang() === 'ru' ? 'Доступ' : 'Access'));
  view.append(access);

  // ---------------------------------------------------------------- link
  const linkInput = el('input', { type: 'text', readonly: true, value: '', spellcheck: 'false' });
  const linkNote = el('div', { class: 'card-hint', text: lang() === 'ru'
    ? 'Ссылка показывается только по кнопке и только этой сессии.'
    : 'The link appears only on request, only in this session.' });
  const revealButton = el('button', {
    class: 'btn btn-primary btn-small', type: 'button', text: lang() === 'ru' ? 'Показать ссылку' : 'Reveal the link',
    onclick: async () => {
      try {
        const data = await api.post('/api/security/link');
        revealed = data.url || '';
        linkInput.value = revealed;
        toast(lang() === 'ru' ? 'Ссылка получена' : 'Link received', 'good');
      } catch (err) { errToast(err); }
    },
  });
  const copyButton = el('button', {
    class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Скопировать' : 'Copy',
    onclick: () => {
      if (!linkInput.value) {
        toast(lang() === 'ru' ? 'Сначала покажи ссылку' : 'Reveal the link first', 'warn');
        return;
      }
      copyText(linkInput.value);
    },
  });
  const rotateButton = el('button', {
    class: 'btn btn-danger btn-small', type: 'button', text: lang() === 'ru' ? 'Новая ссылка' : 'New link',
    onclick: async () => {
      if (!await confirmDialog({
        title: lang() === 'ru' ? 'Сменить ссылку?' : 'Replace the link?',
        message: lang() === 'ru'
          ? 'Старая ссылка перестанет работать. Этот браузер останется в системе; другие сессии тоже — их подпись не меняется.'
          : 'The old link stops working. This browser stays signed in; other sessions keep their signature too.',
        confirmLabel: lang() === 'ru' ? 'Сменить' : 'Replace', danger: true,
      })) return;
      try {
        const data = await api.post('/api/security/rotate');
        revealed = data.url || '';
        linkInput.value = revealed;
        toast(lang() === 'ru' ? 'Ссылка заменена' : 'Link replaced', 'good');
        await load();
      } catch (err) { errToast(err); }
    },
  });
  const linkCard = el('div', { class: 'card', style: { marginTop: '14px' } },
    el('h3', { text: lang() === 'ru' ? 'Ссылка с токеном' : 'The token link' }),
    el('div', { class: 'row' }, linkInput, revealButton, copyButton),
    el('div', { class: 'row', style: { marginTop: '8px' } }, rotateButton, linkNote));
  view.append(linkCard);

  // ---------------------------------------------------------------- audit
  const auditBox = el('div', {});
  const auditCard = el('div', { class: 'card', style: { marginTop: '14px' } },
    el('div', { class: 'row between' },
      el('h3', { text: lang() === 'ru' ? 'Журнал доступа' : 'Access audit' }),
      el('button', {
        class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Обновить' : 'Refresh',
        onclick: () => load(),
      })),
    auditBox);
  view.append(auditCard);

  // ---------------------------------------------------------------- notes
  const notes = el('div', { class: 'card', style: { marginTop: '14px' } },
    el('h3', { text: lang() === 'ru' ? 'Как она защищена' : 'How it is guarded' }),
    el('ul', { class: 'stack', style: { paddingLeft: '18px', margin: 0 } },
      ...(lang() === 'ru' ? [
        'Слушает 127.0.0.1, пока ты сам не откроешь локальную сеть.',
        'Токен 32 байта; файл webpanel.json — права 0600.',
        'Сессионная cookie HttpOnly + SameSite=Strict, подпись HMAC.',
        'Любое изменение требует заголовок X-Yumi-CSRF.',
        'CSP запрещает чужие скрипты, X-Frame-Options запрещает встраивание.',
        'Десять неверных токенов с одного адреса — минута тишины.',
        'Каждое действие пишется в web-audit.log.',
      ] : [
        'Listens on 127.0.0.1 until you open the LAN yourself.',
        'A 32-byte token; webpanel.json is mode 0600.',
        'HttpOnly SameSite=Strict session cookie with an HMAC signature.',
        'Every mutation needs the X-Yumi-CSRF header.',
        'CSP forbids foreign scripts; X-Frame-Options forbids embedding.',
        'Ten wrong tokens from one address earn a minute of silence.',
        'Every action lands in web-audit.log.',
      ]).map((text) => el('li', { text }))));
  view.append(notes);

  // ---------------------------------------------------------------- draw
  function drawAccess() {
    access.replaceChildren();
    const schema = ctx.schema?.settings || [];
    const picked = WEB_KEYS.map((key) => schema.find((entry) => entry.key === key)).filter(Boolean);
    for (const entry of picked) {
      const value = status.settings?.[entry.key];
      access.append(buildSetting(entry, value, async (next) => {
        const result = await ctx.applySettings({ [entry.key]: next });
        if (result?.result?.saved) {
          status.settings = { ...(status.settings || {}), ...result.result.saved };
          if (['web_panel_host', 'web_panel_port', 'web_panel'].includes(entry.key)) {
            toast(lang() === 'ru'
              ? 'Панель перезапускается. Если порт сменился, открой новую ссылку.'
              : 'The panel is restarting. If the port changed, open the new link.', 'warn');
          }
        }
      }));
    }
    access.append(el('p', { class: 'card-hint', text: lang() === 'ru'
      ? 'Смена адреса или порта перезапускает HTTP-сервер; текущая вкладка может потерять соединение.'
      : 'Changing the address or port restarts the HTTP server; this tab may lose its connection.' }));
  }

  function drawWarnings() {
    warnings.replaceChildren();
    const host = security?.host || status.panel?.host || status.settings?.web_panel_host;
    if (host && !['127.0.0.1', '::1'].includes(host)) {
      warnings.append(el('div', { class: 'warn-box' },
        el('b', { text: lang() === 'ru' ? 'Панель открыта в локальную сеть' : 'The panel is exposed to the LAN' }),
        el('div', { text: lang() === 'ru'
          ? `Слушает ${host}. Любой в сети может попробовать подобрать токен. Держи файрвол закрытым, а ссылку — при себе.`
          : `Listening on ${host}. Anyone on the network may try the token. Keep the firewall closed and the link private.` })));
    }
    if (!status.settings?.web_panel) {
      warnings.append(el('div', { class: 'ok-box', text: lang() === 'ru'
        ? 'Сейчас панель выключена: порт не открыт. Включить можно ниже или в настройках Юми.'
        : 'The panel is off right now: no port is open. Enable it below or in Yumi’s own settings.' }));
    }
    if (status.panel?.error) {
      warnings.append(el('div', { class: 'warn-box' }, el('b', { text: lang() === 'ru' ? 'Ошибка запуска' : 'Start error' }),
        el('div', { text: status.panel.error })));
    }
  }

  function drawPanel() {
    const panel = security?.panel || status.panel || {};
    const auth = security?.auth || {};
    panelInfo.replaceChildren(kv([
      [lang() === 'ru' ? 'Слушает' : 'Listening', panel.running
        ? `${panel.host || '?'}:${panel.port || '?'}` : (lang() === 'ru' ? 'выключена' : 'off')],
      [lang() === 'ru' ? 'Адрес' : 'Address', panel.url || '—'],
      ['Token', panel.token_set
        ? badge(lang() === 'ru' ? 'задан' : 'set', 'mint')
        : badge(lang() === 'ru' ? 'нет' : 'none', 'red')],
      [lang() === 'ru' ? 'Сессия' : 'Session', `${auth.session_hours ?? panel.session_hours ?? '—'} ${lang() === 'ru' ? 'ч' : 'h'}`],
      [lang() === 'ru' ? 'Токен создан' : 'Token created', fmtDate(auth.created)],
      [lang() === 'ru' ? 'Токен сменён' : 'Token rotated', auth.rotated ? fmtDate(auth.rotated) : '—'],
      [lang() === 'ru' ? 'Файл доступа' : 'Access file', auth.path || '—'],
      [lang() === 'ru' ? 'Старт' : 'Started', panel.started_at ? fmtDate(panel.started_at) : '—'],
    ]));
  }

  function drawAudit() {
    const items = (security?.audit || []).slice().reverse();
    if (!items.length) {
      auditBox.replaceChildren(emptyBox(lang() === 'ru' ? 'Журнал пуст.' : 'The audit is empty.'));
      return;
    }
    const table = el('table', { class: 'audit', style: { width: '100%', borderCollapse: 'collapse' } });
    for (const item of items.slice(0, 60)) {
      table.append(el('tr', {},
        el('td', { text: item.ts || '' }),
        el('td', { text: item.ip || '' }),
        el('td', {}, badge(item.action || '?', item.action === 'login' ? 'mint' : '')),
        el('td', { text: item.detail || '' })));
    }
    auditBox.replaceChildren(table);
  }

  async function load() {
    try {
      security = await api.get('/api/security');
      drawWarnings();
      drawPanel();
      drawAudit();
    } catch (err) {
      errToast(err);
    }
  }

  const offStatus = ctx.onStatus((next) => {
    status = next;
    drawWarnings();
    drawPanel();
  });

  status = ctx.status || status;
  drawAccess();
  drawWarnings();
  drawPanel();
  load();

  return () => { offStatus(); };
}
