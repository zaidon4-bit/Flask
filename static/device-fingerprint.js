// Browser fingerprint is only a review hint; it never authorizes a device by itself.
// An Android APK may additionally expose a native UUID via an origin-restricted WebMessageListener.
(() => {
  let cachedFingerprint = null;
  let cachedInstallationId = null;

  const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

  function canvasSignal() {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = 240;
      canvas.height = 48;
      const ctx = canvas.getContext('2d');
      if (!ctx) return 'no-canvas';
      ctx.textBaseline = 'top';
      ctx.font = '16px Arial, sans-serif';
      ctx.fillStyle = '#f60';
      ctx.fillRect(10, 5, 90, 26);
      ctx.fillStyle = '#069';
      ctx.fillText('Infinite Academy ∑ منصة', 4, 8);
      ctx.fillStyle = 'rgba(102, 204, 0, 0.6)';
      ctx.fillText('Infinite Academy ∑ منصة', 6, 10);
      return canvas.toDataURL();
    } catch (_error) {
      return 'canvas-error';
    }
  }

  function webglSignal() {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) return 'no-webgl';
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      return `${vendor}~${renderer}`;
    } catch (_error) {
      return 'webgl-error';
    }
  }

  async function sha256Hex(text) {
    const bytes = new TextEncoder().encode(text);
    const digest = await window.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  async function computeFingerprint() {
    if (!window.crypto || !window.crypto.subtle) return null;
    try {
      const longSide = Math.max(window.screen.width, window.screen.height);
      const shortSide = Math.min(window.screen.width, window.screen.height);
      const signals = [
        `${longSide}x${shortSide}x${window.screen.colorDepth}`,
        Intl.DateTimeFormat().resolvedOptions().timeZone || '',
        navigator.hardwareConcurrency || '',
        navigator.maxTouchPoints || 0,
        navigator.platform || '',
        webglSignal(),
        canvasSignal(),
      ];
      return await sha256Hex(signals.join('||'));
    } catch (_error) {
      return null;
    }
  }

  function getFingerprint() {
    if (!cachedFingerprint) cachedFingerprint = computeFingerprint();
    return cachedFingerprint;
  }

  // The Android app must expose an origin-restricted WebMessageListener named
  // InfiniteAcademyNative. Native requests carry requestId and return a JSON response.
  // The private signing key never leaves Android Keystore.
  function bridgeAvailable() {
    return !!(window.InfiniteAcademyNative && typeof window.InfiniteAcademyNative.postMessage === 'function');
  }

  function callNative(type, params = {}) {
    return new Promise((resolve, reject) => {
      const bridge = window.InfiniteAcademyNative;
      if (!bridgeAvailable()) return reject(new Error('native_bridge_unavailable'));
      const requestId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
      const previousHandler = bridge.onmessage;
      let finished = false;
      const finish = (callback, value) => {
        if (finished) return;
        finished = true;
        window.clearTimeout(timeoutId);
        bridge.onmessage = previousHandler || null;
        callback(value);
      };
      const timeoutId = window.setTimeout(() => finish(reject, new Error('native_bridge_timeout')), 5000);
      bridge.onmessage = (event) => {
        let response;
        try { response = JSON.parse(String(event && event.data !== undefined ? event.data : '')); }
        catch (_error) { if (typeof previousHandler === 'function') previousHandler.call(bridge, event); return; }
        if (!response || response.requestId !== requestId) {
          if (typeof previousHandler === 'function') previousHandler.call(bridge, event);
          return;
        }
        if (response.ok !== true) return finish(reject, new Error(response.error || 'native_operation_failed'));
        finish(resolve, response);
      };
      try { bridge.postMessage(JSON.stringify({ requestId, type, ...params })); }
      catch (error) { finish(reject, error); }
    });
  }

  async function getNativeIdentity() {
    const result = await callNative('get-device-identity');
    if (!result.installationId || !UUID_V4_RE.test(result.installationId) ||
        !result.publicKey || typeof result.publicKey !== 'string' || result.publicKey.length > 2048) {
      throw new Error('invalid_native_identity');
    }
    return { installationId: result.installationId.toLowerCase(), publicKey: result.publicKey };
  }

  async function signNativeChallenge(challenge) {
    const result = await callNative('sign-device-challenge', { challenge });
    if (!result.signature || typeof result.signature !== 'string' || result.signature.length > 512) {
      throw new Error('invalid_native_signature');
    }
    return result.signature;
  }

  function showNativeProofError(form, message) {
    const output = form.querySelector('[data-native-proof-error]');
    if (output) { output.textContent = message; output.hidden = false; }
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = false;
    form.dataset.deviceIdentityReady = 'false';
  }

  window.InfiniteAcademyDevice = {
    getFingerprint,
    bridgeAvailable,
    getNativeIdentity,
    signNativeChallenge,
  };

  document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('form input[name="fp"]')?.form;
    if (!form) return;
    const fingerprintInput = form.querySelector('input[name="fp"]');
    const installationInput = form.querySelector('input[name="app_installation_id"]');
    if (!fingerprintInput || !installationInput) return;

    const fingerprintPromise = getFingerprint();
    fingerprintPromise.then((fingerprint) => { if (fingerprint) fingerprintInput.value = fingerprint; });

    form.addEventListener('submit', async (event) => {
      if (form.dataset.deviceIdentityReady === 'true') return;
      event.preventDefault();
      const submit = form.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      const fingerprint = await fingerprintPromise;
      if (fingerprint) fingerprintInput.value = fingerprint;

      // Normal browsers continue using the website cookie policy. Official APKs fail closed if
      // their native challenge/signature path is unavailable; they never silently downgrade.
      if (!bridgeAvailable()) {
        form.dataset.deviceIdentityReady = 'true';
        HTMLFormElement.prototype.submit.call(form);
        return;
      }

      try {
        const identity = await getNativeIdentity();
        const csrf = form.querySelector('input[name="csrf_token"]')?.value || '';
        const email = form.querySelector('input[name="email"]')?.value?.trim().toLowerCase() || '';
        if (!csrf || !email) throw new Error('missing_login_data');
        const challengeResponse = await fetch('/api/device/challenge', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
          body: JSON.stringify({ email, installation_id: identity.installationId, public_key: identity.publicKey }),
          cache: 'no-store',
        });
        const challenge = await challengeResponse.json().catch(() => ({}));
        if (!challengeResponse.ok || !challenge.challenge_id || !challenge.challenge) {
          throw new Error(challenge.message || 'challenge_request_failed');
        }
        const signature = await signNativeChallenge(challenge.challenge);
        const set = (name, value) => {
          const input = form.querySelector(`input[name="${name}"]`);
          if (input) input.value = value;
        };
        set('app_installation_id', identity.installationId);
        set('app_public_key', challenge.public_key || identity.publicKey);
        set('app_challenge_id', challenge.challenge_id);
        set('app_challenge', challenge.challenge);
        set('app_signature', signature);
        form.dataset.deviceIdentityReady = 'true';
        HTMLFormElement.prototype.submit.call(form);
      } catch (_error) {
        showNativeProofError(form, 'تعذّر التحقق الآمن من هذا التثبيت. تأكد من تحديث التطبيق واتصال الإنترنت، ثم حاول مجدداً.');
      }
    });
  });
})();
