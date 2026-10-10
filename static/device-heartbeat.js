(() => {
  const script = document.currentScript;
  if (!script) return;

  const heartbeatUrl = script.dataset.heartbeatUrl;
  const loginUrl = script.dataset.loginUrl;
  const csrfToken = script.dataset.csrfToken;
  if (!heartbeatUrl || !csrfToken) return;

  let requestInFlight = false;
  let stopped = false;

  async function sendHeartbeat() {
    if (stopped || requestInFlight || document.visibilityState !== 'visible') return;
    requestInFlight = true;
    try {
      const headers = { 'X-CSRFToken': csrfToken, 'Accept': 'application/json' };
      const fingerprint = window.InfiniteAcademyDevice ? await window.InfiniteAcademyDevice.getFingerprint() : null;
      if (fingerprint) headers['X-Device-Fingerprint'] = fingerprint;
      const response = await fetch(heartbeatUrl, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers
      });
      if (response.status === 401 || response.status === 403) {
        stopped = true;
        window.clearInterval(timerId);
        // 401 = this session was replaced by a newer login: go back to the login page.
        if (response.status === 401 && loginUrl) window.location.href = loginUrl;
      }
    } catch (_error) {
      // A temporary connection failure should not stop future heartbeat attempts.
    } finally {
      requestInFlight = false;
    }
  }

  const timerId = window.setInterval(sendHeartbeat, 30_000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sendHeartbeat();
  });
  sendHeartbeat();
})();
