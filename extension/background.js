// The extension's one long-lived piece: it holds the token, talks to the server, and runs
// the form script in the posting's frames. The side panel asks it for everything.

const DEFAULT_SERVER = 'https://jobstager.onrender.com';

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

async function settings() {
  const { server, token } = await chrome.storage.local.get(['server', 'token']);
  return { server: server || DEFAULT_SERVER, token: token || null };
}

// A free Render instance sleeps after 15 idle minutes and takes about a minute to wake,
// so requests get a long timeout rather than failing while it boots.
async function api(path, { method = 'GET', body, raw = false, timeout = 90000 } = {}) {
  const { server, token } = await settings();
  if (!token) throw Object.assign(new Error('Not connected.'), { status: 401 });
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(server + path, {
      method,
      signal: ctrl.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      await chrome.storage.local.remove('token');
      throw Object.assign(new Error('The connection expired. Connect again.'), { status: 401 });
    }
    if (!res.ok) {
      const detail = await res.json().then((d) => d.detail).catch(() => null);
      throw Object.assign(new Error(detail || `Server answered ${res.status}.`), { status: res.status });
    }
    return raw ? res : res.json();
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('JobStager did not answer in time.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// Every frame of the tab that the extension may script. Greenhouse postings on a
// company's own site live in an iframe, so the form is often not in the top frame.
async function inFrames(tabId, func, args = []) {
  await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    files: ['form.js'],
  }).catch(() => {});
  const results = await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    func,
    args,
  });
  return results.filter((r) => r.result != null);
}

async function scan(tabId) {
  const frames = await inFrames(tabId, () => globalThis.__jobstager?.scan() ?? null);
  // Refs are unique per frame only; prefix the frame so the fill step can route back.
  const fields = [];
  const files = [];
  let pageText = '';
  let url = '';
  for (const { frameId, result } of frames) {
    for (const f of result.fields) fields.push({ ...f, ref: `${frameId}:${f.ref}` });
    for (const f of result.files) files.push({ ...f, ref: `${frameId}:${f.ref}` });
    if (result.fields.length && result.pageText.length > pageText.length) {
      pageText = result.pageText;
      url = result.url;
    }
  }
  return { fields, files, pageText, url };
}

const handlers = {
  async connected(msg) {
    await chrome.storage.local.set({ token: msg.token, server: msg.server });
    chrome.runtime.sendMessage({ type: 'state-changed' }).catch(() => {});
    return { ok: true };
  },
  async status() {
    const { server, token } = await settings();
    if (!token) return { connected: false, server };
    const me = await api('/v1/me');
    return { connected: true, server, me };
  },
  async disconnect() {
    await api('/v1/token', { method: 'DELETE' }).catch(() => {});
    await chrome.storage.local.remove('token');
    return { ok: true };
  },
  async setServer(msg) {
    const server = msg.server.replace(/\/+$/, '');
    await chrome.storage.local.set({ server });
    await chrome.storage.local.remove('token');
    return { ok: true, server };
  },
  async openConnect() {
    const { server } = await settings();
    await chrome.tabs.create({ url: `${server}/connect-extension` });
    return { ok: true };
  },
  async scan(msg) {
    const found = await scan(msg.tabId);
    if (!found.fields.length) return { ...found, resolved: null };
    const resolved = await api('/v1/resolve', {
      method: 'POST',
      body: {
        url: found.url,
        page_text: found.pageText,
        grad_year: msg.gradYear || null,
        fields: found.fields.map(({ ref, kind, question, offered, required, max_length }) =>
          ({ ref, kind, question, offered, required, max_length })),
      },
    });
    return { ...found, resolved };
  },
};

const CONNECT_PAGES = [
  'https://jobstager.onrender.com/connect-extension',
  'http://127.0.0.1:8000/connect-extension',
  'http://localhost:8000/connect-extension',
];

// A message from a web page's frame (sender.tab set) may only deliver a token, and only
// from JobStager's own connect page. Everything else comes from the side panel.
function allowed(msg, sender) {
  if (!sender.tab) return sender.id === chrome.runtime.id;
  return msg.type === 'connected' && CONNECT_PAGES.some((p) => (sender.url || '').startsWith(p));
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const handler = handlers[msg?.type];
  if (!handler || !allowed(msg, sender)) return false;
  handler(msg)
    .then(reply)
    .catch((err) => reply({ error: err.message, status: err.status || null }));
  return true;
});
