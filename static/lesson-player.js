(() => {
  const frame = document.getElementById('lesson-video-frame');
  const status = document.getElementById('lesson-video-status');
  if (!frame || !status) return;

  const playbackUrl = frame.dataset.playbackUrl;
  const csrfToken = frame.dataset.csrfToken;

  function showError(message) {
    const panel = document.createElement('div');
    panel.className = 'notice';
    panel.setAttribute('role', 'alert');
    panel.textContent = message || 'تعذّر تشغيل الفيديو. حدّث الصفحة أو سجّل الدخول مجدداً.';
    frame.replaceChildren(panel);
  }

  async function loadPlayer() {
    try {
      const response = await fetch(playbackUrl, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken
        },
        body: '{}'
      });

      const contentType = response.headers.get('content-type') || '';
      if (response.redirected || !contentType.includes('application/json')) {
        window.location.assign('/login');
        return;
      }

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) {
          window.location.assign('/login');
          return;
        }
        throw new Error(payload.message || 'هذا الحساب أو الجهاز غير مخوّل لمشاهدة الفيديو.');
      }
      if (!payload.otp || !payload.playback_info) {
        throw new Error('لم تصل بيانات تشغيل الفيديو بشكل صحيح.');
      }

      const playerUrl = new URL('https://player.vdocipher.com/v2/');
      playerUrl.searchParams.set('otp', payload.otp);
      playerUrl.searchParams.set('playbackInfo', payload.playback_info);

      const iframe = document.createElement('iframe');
      iframe.src = playerUrl.toString();
      iframe.allow = 'encrypted-media; autoplay; fullscreen; picture-in-picture';
      iframe.allowFullscreen = true;
      iframe.title = 'VdoCipher protected lesson';
      frame.replaceChildren(iframe);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'تعذّر تشغيل الفيديو حالياً.');
    }
  }

  loadPlayer();
})();
