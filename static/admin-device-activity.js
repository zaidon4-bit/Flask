(() => {
  const script = document.currentScript;
  const activityUrl = script && script.dataset.activityUrl;
  if (!activityUrl) return;

  let requestInFlight = false;

  async function refreshActivity() {
    if (requestInFlight || document.visibilityState !== 'visible') return;
    requestInFlight = true;
    try {
      const response = await fetch(activityUrl, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      });
      if (!response.ok) return;
      const data = await response.json();
      const activeIds = new Set((data.active_device_ids || []).map(String));
      const total = document.querySelector('[data-active-device-count]');
      if (total) total.textContent = String(data.active_device_count ?? 0);

      // New sharing alert since the page was loaded: show the banner.
      const marker = document.querySelector('[data-sharing-alert-total]');
      const banner = document.querySelector('[data-sharing-alert-new]');
      if (marker && banner && Number(data.sharing_alert_total || 0) > Number(marker.dataset.sharingAlertTotal || 0)) {
        banner.hidden = false;
      }

      // Alert when a new pending device appears while the admin dashboard is open.
      const pendingMarker = document.querySelector('[data-pending-device-ids]');
      const pendingBanner = document.querySelector('[data-device-requests-new]');
      if (pendingMarker && pendingBanner) {
        const previousPendingIds = new Set(
          (pendingMarker.dataset.pendingDeviceIds || '').split(',').filter(Boolean)
        );
        const currentPendingIds = (data.pending_device_ids || []).map(String);
        if (currentPendingIds.some((id) => !previousPendingIds.has(id))) {
          pendingBanner.hidden = false;
        }
        pendingMarker.dataset.pendingDeviceIds = currentPendingIds.join(',');
      }

      document.querySelectorAll('[data-user-active-count]').forEach((element) => {
        const userId = element.dataset.userActiveCount;
        element.textContent = String((data.active_by_user || {})[userId] || 0);
      });

      document.querySelectorAll('[data-device-active]').forEach((element) => {
        const isActive = activeIds.has(element.dataset.deviceActive);
        element.classList.toggle('active', isActive);
        element.textContent = isActive ? 'نشط الآن' : 'غير نشط';
      });
    } catch (_error) {
      // Keep the latest known status if a temporary network error occurs.
    } finally {
      requestInFlight = false;
    }
  }

  const timerId = window.setInterval(refreshActivity, 15_000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshActivity();
  });
  refreshActivity();
})();
