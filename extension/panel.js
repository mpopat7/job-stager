// The side panel only renders; the background worker owns the token and the server.

const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg);

function notice(message, bad = false) {
  $('notice').textContent = message || '';
  $('notice').className = bad ? 'bad' : '';
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function refresh() {
  // The server may be asleep; say so rather than showing a dead panel for a minute.
  const slow = setTimeout(() => notice('Waking the JobStager server. This can take up to a minute...'), 2500);
  const st = await send({ type: 'status' });
  clearTimeout(slow);
  notice('');
  if (st?.error && st.status !== 401) {
    notice(st.error, true);
  }
  const connected = !!st?.connected;
  $('signed-out').hidden = connected;
  $('signed-in').hidden = !connected;
  $('server').value = st?.server || '';
  $('who').textContent = connected ? st.me.name || '' : '';
  if (connected) {
    const cohort = $('cohort');
    cohort.length = 1;
    for (const year of st.me.cohorts || []) cohort.add(new Option(`Class of ${year}`, year));
  }
}

function describe(field, answer) {
  const li = document.createElement('li');
  const value = answer?.check === true ? 'Tick'
    : answer?.check === false ? 'Leave unticked'
    : answer?.value ?? null;
  const flagged = answer && (answer.needs_attention || !answer.confident);
  li.className = value == null ? 'empty' : flagged ? 'flagged' : 'filled';
  const q = document.createElement('div');
  q.className = 'q';
  q.textContent = field.question || '(no label)';
  const a = document.createElement('div');
  a.className = 'a';
  a.textContent = value == null ? 'Nothing to fill' : value;
  li.append(q, a);
  if (value != null && flagged) {
    const why = document.createElement('div');
    why.className = 'why';
    why.textContent = answer.needs_attention ? 'Your profile has nothing for this' : 'A guess; check it';
    li.append(why);
  }
  return li;
}

async function scanPage() {
  const tab = await activeTab();
  $('scan').disabled = true;
  $('fields').replaceChildren();
  $('summary').textContent = 'Scanning...';
  const res = await send({ type: 'scan', tabId: tab.id, gradYear: Number($('cohort').value) || null });
  $('scan').disabled = false;
  if (res?.error) {
    $('summary').textContent = '';
    notice(res.error, true);
    if (res.status === 401) refresh();
    return;
  }
  notice('');
  if (!res.fields.length) {
    $('summary').textContent = 'No application form found on this page.';
    return;
  }
  const answers = new Map(res.resolved.answers.map((a) => [a.ref, a]));
  const resumes = res.files.length ? `, ${res.files.length} file upload${res.files.length > 1 ? 's' : ''}` : '';
  $('summary').textContent = `${res.fields.length} fields${resumes}. ${res.resolved.filled} answered, `
    + `${res.resolved.flagged} to check. Resume: class of ${res.resolved.grad_year}.`;
  $('fields').replaceChildren(...res.fields.map((f) => describe(f, answers.get(f.ref))));
}

$('connect').addEventListener('click', () => send({ type: 'openConnect' }));
$('disconnect').addEventListener('click', async () => { await send({ type: 'disconnect' }); refresh(); });
$('scan').addEventListener('click', scanPage);
$('server-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  await send({ type: 'setServer', server: $('server').value.trim() });
  refresh();
});
chrome.runtime.onMessage.addListener((msg) => { if (msg?.type === 'state-changed') refresh(); });

refresh();
