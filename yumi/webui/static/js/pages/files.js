import { api } from '../api.js';
import {
  el, tr, icon, fmtBytes, fmtDate, errToast, toast, sectionTitle, emptyBox, modal, button, badge,
} from '../ui.js';

const TARGETS = {
  image: ['png', 'jpg', 'webp', 'gif', 'ico'],
  video: ['mp4', 'webm', 'mkv', 'gif', 'mp3', 'opus'],
  audio: ['mp3', 'ogg', 'opus', 'flac', 'wav', 'm4a'],
};
const PRESET_EN = {
  'маленькая картинка': 'small picture', 'до 1 МБ': 'up to 1 MB',
  'вполовину': 'half size', 'до 10 МБ': 'up to 10 MB', 'до 25 МБ': 'up to 25 MB',
  'до 50 МБ': 'up to 50 MB',
};
const PRESETS = {
  image: [
    ['p_small', 'маленькая картинка'],
    ['p_1mb', 'до 1 МБ'],
    ['p_half', 'вполовину'],
  ],
  video: [
    ['p_10', 'до 10 МБ'],
    ['p_25', 'до 25 МБ'],
    ['p_50', 'до 50 МБ'],
    ['p_720', '720p'],
    ['p_480', '480p'],
  ],
  audio: [
    ['p_opus', 'opus'],
    ['p_mp3', 'mp3'],
  ],
};

export function render(view, ctx) {
  const lang = () => ctx.lang;
  let tab = 'recent';
  let items = [];
  let shelfPaths = new Set();
  let loading = false;

  view.append(
    el('h2', { class: 'pagehead', text: lang() === 'ru' ? 'Файлы' : 'Files' }),
    el('p', { class: 'pagelead', text: lang() === 'ru'
      ? 'Недавние, полка, конвертация и сжатие. Файлы можно бросить прямо в окно.'
      : 'Recent, the shelf, convert and compress. Drop files straight into the window.' }),
  );

  // ---------------------------------------------------------------- jobs
  const jobsBox = el('div', { class: 'jobs', text: lang() === 'ru' ? 'очередь свободна' : 'the queue is idle' });
  const jobsCard = el('div', { class: 'card', style: { marginBottom: '16px' } },
    el('div', { class: 'row between' },
      el('h3', { text: lang() === 'ru' ? 'Очередь обработки' : 'Processing queue' }),
      el('button', {
        class: 'btn btn-small btn-danger', type: 'button', text: lang() === 'ru' ? 'Отменить всё' : 'Cancel all',
        onclick: async () => {
          try {
            await api.post('/api/files/jobs/cancel');
            toast(lang() === 'ru' ? 'Очередь остановлена' : 'Queue stopped', 'good');
            ctx.refreshStatus();
          } catch (err) { errToast(err); }
        },
      })),
    jobsBox);
  view.append(jobsCard);

  // ---------------------------------------------------------------- drop + tabs
  const fileInput = el('input', { type: 'file', multiple: true, hidden: true });
  const dropText = el('div', {}, el('b', { text: lang() === 'ru' ? 'Брось файлы сюда' : 'Drop files here' }),
    el('div', { class: 'card-hint', text: lang() === 'ru' ? 'или нажми, чтобы выбрать' : 'or click to choose' }));
  const dropzone = el('div', { class: 'dropzone' }, dropText, fileInput);
  const uploadNote = el('div', { class: 'card-hint', text: '' });

  const tabs = el('div', { class: 'tabs' });
  const tabRecent = el('button', { class: 'tab active', type: 'button', text: lang() === 'ru' ? 'Недавние' : 'Recent' });
  const tabShelf = el('button', { class: 'tab', type: 'button', text: lang() === 'ru' ? 'Полка' : 'Shelf' });
  tabs.append(tabRecent, tabShelf,
    el('span', { class: 'spacer' }),
    el('button', {
      class: 'btn btn-small', type: 'button', text: lang() === 'ru' ? 'Обновить' : 'Refresh',
      onclick: () => load(),
    }));

  const grid = el('div', { class: 'files' });
  view.append(dropzone, uploadNote, tabs, grid);

  dropzone.addEventListener('click', (ev) => {
    if (ev.target !== fileInput) fileInput.click();
  });
  fileInput.addEventListener('change', () => {
    upload([...fileInput.files]);
    fileInput.value = '';
  });
  for (const name of ['dragenter', 'dragover']) {
    dropzone.addEventListener(name, (ev) => {
      ev.preventDefault();
      dropzone.classList.add('hot');
    });
  }
  for (const name of ['dragleave', 'drop']) {
    dropzone.addEventListener(name, (ev) => {
      ev.preventDefault();
      if (name === 'drop' && ev.dataTransfer?.files?.length) upload([...ev.dataTransfer.files]);
      dropzone.classList.remove('hot');
    });
  }

  tabRecent.addEventListener('click', () => {
    tab = 'recent';
    tabRecent.classList.add('active');
    tabShelf.classList.remove('active');
    load();
  });
  tabShelf.addEventListener('click', () => {
    tab = 'shelf';
    tabShelf.classList.add('active');
    tabRecent.classList.remove('active');
    load();
  });

  async function upload(files) {
    if (!files.length) return;
    let lastPath = '';
    for (const file of files) {
      uploadNote.textContent = `${lang() === 'ru' ? 'загружаю' : 'uploading'} ${file.name}…`;
      try {
        const data = await api.upload(file);
        lastPath = data.path;
        uploadNote.textContent = `${lang() === 'ru' ? 'готово' : 'done'}: ${data.path}`;
        toast(lang() === 'ru' ? `Загружено: ${file.name}` : `Uploaded: ${file.name}`, 'good');
        try {
          await api.post('/api/files/shelf', { path: data.path, add: true });
        } catch {
          // the file is still in the cache; the shelf is a convenience
        }
      } catch (err) {
        uploadNote.textContent = `${file.name}: ${err.message}`;
        errToast(err);
      }
    }
    if (!lastPath) return;
    tab = 'shelf';
    tabShelf.classList.add('active');
    tabRecent.classList.remove('active');
    await load();
  }

  async function loadShelf() {
    try {
      const data = await api.get('/api/files/shelf');
      shelfPaths = new Set((data.items || []).map((f) => f.path));
    } catch {
      shelfPaths = new Set();
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    grid.replaceChildren(el('div', { class: 'loading', text: lang() === 'ru' ? 'читаю полки…' : 'reading the shelves…' }));
    try {
      await loadShelf();
      const data = await api.get(tab === 'shelf' ? '/api/files/shelf' : '/api/files/recent');
      items = data.items || [];
      draw();
    } catch (err) {
      grid.replaceChildren(emptyBox(err.message));
    } finally {
      loading = false;
    }
  }

  function draw() {
    grid.replaceChildren();
    if (!items.length) {
      grid.append(emptyBox(tab === 'shelf'
        ? (lang() === 'ru' ? 'Полка пуста. Добавь файл кнопкой «на полку».' : 'The shelf is empty. Add a file with “to shelf”.')
        : (lang() === 'ru' ? 'Недавних файлов пока нет.' : 'No recent files yet.')));
      return;
    }
    for (const file of items) grid.append(fileCard(file));
  }

  function fileCard(file) {
    const thumb = el('div', { class: 'thumb' });
    if (file.kind === 'image' && file.exists) {
      const img = el('img', { src: `/api/file/preview?path=${encodeURIComponent(file.path)}`, alt: '', loading: 'lazy' });
      img.addEventListener('click', () => window.open(img.src, '_blank', 'noopener'));
      thumb.append(img);
    } else {
      thumb.append(el('span', { text: (file.ext || file.kind || 'file').toUpperCase().slice(0, 6) }));
    }
    const onShelf = shelfPaths.has(file.path);
    const action = (kind, label) => el('button', {
      class: 'btn btn-small', type: 'button',
      text: lang() === 'ru' ? label : ({
        'Открыть': 'Open', 'С полки': 'Unpin', 'На полку': 'Shelf',
        'Формат': 'Format', 'Сжать': 'Compress',
      })[label] || label,
      onclick: () => actionFor(kind, file),
    });
    const actions = el('div', { class: 'file-actions' },
      action('open', 'Открыть'),
      action('shelf', onShelf ? 'С полки' : 'На полку'),
      file.kind && TARGETS[file.kind] ? action('convert', 'Формат') : null,
      file.kind && PRESETS[file.kind] ? action('compress', 'Сжать') : null,
    );
    return el('div', { class: 'file-card' },
      thumb,
      el('div', { class: 'file-body' },
        el('b', { text: file.name, title: file.path }),
        el('small', { text: `${fmtBytes(file.size)} · ${fmtDate(file.mtime)}` }),
        el('div', { class: 'row' },
          el('span', { class: 'kind', text: file.kind || 'file' }),
          file.exists ? null : badge(lang() === 'ru' ? 'нет файла' : 'missing', 'red')),
        actions));
  }

  async function actionFor(kind, target) {
    if (!target) return;
    if (kind === 'open') {
      try {
        await api.post('/api/files/open', { path: target.path });
        toast(lang() === 'ru' ? 'Открываю' : 'Opening', 'good');
      } catch (err) { errToast(err); }
      return;
    }
    if (kind === 'shelf') {
      const onShelf = shelfPaths.has(target.path);
      try {
        await api.post('/api/files/shelf', { path: target.path, add: !onShelf });
        toast(onShelf
          ? (lang() === 'ru' ? 'Убрала с полки' : 'Removed from the shelf')
          : (lang() === 'ru' ? 'Положила на полку' : 'Added to the shelf'), 'good');
        await load();
      } catch (err) { errToast(err); }
      return;
    }
    if (kind === 'convert' || kind === 'compress') {
      openJobDialog(target, kind);
    }
  }

  function openJobDialog(file, op) {
    const choices = op === 'convert'
      ? TARGETS[file.kind].map((ext) => [ext, ext])
      : PRESETS[file.kind];
    const select = el('select', {});
    for (const [value, label] of choices) {
      select.append(el('option', {
        value,
        text: lang() === 'ru' ? label : (PRESET_EN[label] || label),
      }));
    }
    const body = el('div', {},
      el('p', { class: 'pagelead', text: `${file.name} · ${fmtBytes(file.size)}` }),
      el('label', { class: 'field' }, el('span', { text: op === 'convert' ? (lang() === 'ru' ? 'во что' : 'format') : (lang() === 'ru' ? 'насколько' : 'size') }), select));
    modal({
      title: op === 'convert'
        ? (lang() === 'ru' ? 'Сменить формат' : 'Convert')
        : (lang() === 'ru' ? 'Сжать файл' : 'Compress'),
      body,
      actions: [
        { label: lang() === 'ru' ? 'Отмена' : 'Cancel' },
        {
          label: lang() === 'ru' ? 'В очередь' : 'Queue',
          kind: 'btn-primary',
          onClick: async () => {
            try {
              await api.post('/api/files/job', { op, path: file.path, arg: select.value });
              toast(lang() === 'ru' ? 'В очереди' : 'Queued', 'good');
              ctx.refreshStatus();
            } catch (err) { errToast(err); }
          },
        },
      ],
    });
  }

  function drawJobs(status) {
    const jobs = status?.jobs || {};
    jobsBox.replaceChildren();
    if (jobs.current) {
      const cur = jobs.current;
      const pct = Math.round((cur.fraction || 0) * 100);
      jobsBox.append(el('div', { class: 'job' },
        el('b', { text: `${cur.op} · ${cur.src.split('/').pop()}` }),
        el('div', { class: 'meter' }, el('i', { style: { width: `${pct}%` } })),
        el('small', { class: 'card-hint', text: `${pct}%${cur.arg ? ` · ${cur.arg}` : ''}` })));
    }
    for (const job of (jobs.queued || []).slice(0, 6)) {
      jobsBox.append(el('div', { class: 'job' },
        el('b', { text: `${job.op} · ${job.src.split('/').pop()}` }),
        el('small', { class: 'card-hint', text: job.arg || '' })));
    }
    if (!jobs.current && !(jobs.queued || []).length) {
      jobsBox.append(el('div', { class: 'card-hint', text: lang() === 'ru' ? 'свободна' : 'idle' }));
    }
  }

  // Fix the per-card wiring after drawing (kept simple and explicit).
  const offStatus = ctx.onStatus((status) => drawJobs(status));
  const offJobs = ctx.onEvent('jobs', () => ctx.refreshStatus());
  const offFiles = ctx.onEvent('files', load);
  drawJobs(ctx.status);
  load();

  return () => {
    offStatus();
    offJobs();
    offFiles();
  };
}
