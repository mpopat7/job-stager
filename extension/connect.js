// Runs only on JobStager's own /connect-extension page. The page posts the token it just
// minted to its own window; this hands it to the extension and tells the page it landed.
window.addEventListener('message', async (event) => {
  if (event.source !== window || event.origin !== location.origin) return;
  if (event.data?.type !== 'jobstager-extension-token' || !event.data.token) return;
  const reply = await chrome.runtime.sendMessage({
    type: 'connected',
    token: event.data.token,
    server: location.origin,
  });
  if (reply?.ok) window.postMessage({ type: 'jobstager-extension-connected' }, location.origin);
});
