// The only place that talks HTTP.  Cookies stay HttpOnly; every mutation
// carries the readable CSRF value in a header, as the server demands.

let csrf = null;

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export function setCsrf(value) {
  csrf = value || null;
}

export function getCsrf() {
  return csrf;
}

async function parse(res) {
  const type = res.headers.get('Content-Type') || '';
  if (type.includes('application/json')) {
    return res.json().catch(() => null);
  }
  return res.text();
}

export async function request(method, path, body, opts = {}) {
  const headers = { Accept: 'application/json' };
  const hasBody = body !== undefined;
  if (hasBody) headers['Content-Type'] = 'application/json';
  if (csrf && method !== 'GET' && method !== 'HEAD') headers['X-Yumi-CSRF'] = csrf;
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: hasBody ? JSON.stringify(body) : undefined,
      signal: opts.signal,
      cache: 'no-store',
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    const ru = (document.documentElement.lang || '') === 'ru';
    throw new ApiError(0, ru
      ? 'Юми не отвечает. Проверь, запущено ли приложение.'
      : 'Yumi is not answering. Check that the app is running.', null);
  }
  const data = await parse(res);
  if (!res.ok) {
    const message = (data && (data.error || data.message)) || res.statusText || `HTTP ${res.status}`;
    if (res.status === 401) window.dispatchEvent(new CustomEvent('yumi:unauthorized'));
    throw new ApiError(res.status, String(message), data);
  }
  return data;
}

export const api = {
  get: (path, opts) => request('GET', path, undefined, opts),
  post: (path, body = {}) => request('POST', path, body),
  put: (path, body = {}) => request('PUT', path, body),
  patch: (path, body = {}) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),

  async session() {
    const data = await this.get('/api/session');
    if (data && data.authed && data.csrf) setCsrf(data.csrf);
    return data;
  },

  async login(token) {
    const data = await this.post('/api/login', { token });
    if (data && data.csrf) setCsrf(data.csrf);
    return data;
  },

  async logout() {
    try {
      await this.post('/api/logout');
    } finally {
      setCsrf(null);
    }
  },

  status: () => api.get('/api/status'),
  schema: () => api.get('/api/schema'),

  nluConfirm: (ok) => api.post('/api/nlu/confirm', { ok: Boolean(ok) }),

  notesList: (params = '') => api.get(`/api/notes${params ? `?${params}` : ''}`),
  noteAdd: (text, title) => api.post('/api/notes', { text, title }),
  noteUpdate: (id, text, title) => api.patch(`/api/notes/${id}`, { text, title }),
  noteDelete: (id) => api.del(`/api/notes/${id}`),
  notesClear: () => api.post('/api/notes/clear', { all: true }),

  async patchSettings(patch) {
    return api.patch('/api/settings', { patch });
  },

  resetSetting: (key) => api.post('/api/settings/reset', key ? { key } : { all: true }),

  async upload(file) {
    const query = new URLSearchParams({ name: file.name || 'file' });
    const headers = { 'X-Yumi-CSRF': csrf || '' };
    const res = await fetch(`/api/upload?${query}`, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: file,
    });
    const data = await parse(res);
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new CustomEvent('yumi:unauthorized'));
      const ru = (document.documentElement.lang || '') === 'ru';
      throw new ApiError(res.status, (data && data.error) || (ru ? 'не удалось загрузить' : 'upload failed'), data);
    }
    return data;
  },
};

export const EVENTS = ['hello', 'status', 'bubble', 'settings', 'files', 'jobs', 'actions', 'chat', 'notes', 'server'];

export function connectEvents(onEvent, onState) {
  let source = null;
  let retry = 0;
  let stopped = false;

  const open = () => {
    if (stopped) return;
    source = new EventSource('/api/events');
    source.addEventListener('open', () => {
      retry = 0;
      onState && onState(true);
    });
    for (const name of EVENTS) {
      source.addEventListener(name, (ev) => {
        let data = null;
        try {
          data = JSON.parse(ev.data);
        } catch {
          data = null;
        }
        onEvent && onEvent(name, data);
      });
    }
    source.addEventListener('error', () => {
      onState && onState(false);
      source.close();
      if (stopped) return;
      retry = Math.min(retry + 1, 8);
      setTimeout(open, 600 * retry);
    });
  };

  open();
  return () => {
    stopped = true;
    if (source) source.close();
  };
}
