(() => {
  const root = document.querySelector('[data-live-runtime]');
  if (!root) return;

  const backoffList = root.querySelector('[data-backoff-list]');
  const formatRemaining = (retryAt) => {
    if (!retryAt) return 'time unavailable';
    const seconds = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
    if (seconds === 0) return 'due now';
    const minutes = Math.floor(seconds / 60);
    return `in ${minutes ? `${minutes}m ` : ''}${seconds % 60}s`;
  };

  const tick = () => {
    root.querySelectorAll('[data-retry-at]').forEach((item) => {
      const target = item.querySelector('[data-countdown]');
      if (target) target.textContent = formatRemaining(Number(item.dataset.retryAt));
    });
  };

  const renderBackoff = (rows) => {
    if (!backoffList) return;
    backoffList.replaceChildren(...rows.map((row) => {
      const article = document.createElement('article');
      article.className = 'provider-backoff';
      article.dataset.retryAt = row.next_retry_ts_ms || '';
      const title = document.createElement('strong');
      title.textContent = `${String(row.provider || 'Provider').toUpperCase()} paused this branch`;
      const label = document.createElement('span');
      label.textContent = row.deferral_label;
      const timing = document.createElement('small');
      timing.className = 'mono';
      timing.append('Retry ');
      const countdown = document.createElement('b');
      countdown.dataset.countdown = '';
      timing.append(countdown);
      article.append(title, label, timing);
      return article;
    }));
    tick();
  };

  const refresh = async () => {
    try {
      const response = await fetch(root.dataset.statusUrl, {headers: {'Accept': 'application/json'}});
      if (!response.ok) return;
      const status = await response.json();
      Object.entries(status.frontier_counts).forEach(([name, value]) => {
        const target = root.querySelector(`[data-count="${name}"]`);
        if (target) target.textContent = String(value);
      });
      renderBackoff(status.provider_backoff || []);
      const strip = root.querySelector('[data-freshness-strip]');
      const state = root.querySelector('[data-freshness-state]');
      strip?.classList.toggle('stale', Boolean(status.data_freshness?.stale));
      if (state) state.textContent = status.data_freshness?.stale
        ? 'Refresh recommended'
        : 'Within configured freshness window';
    } catch (_error) {
      // The sealed snapshot remains visible when the status poll is unavailable.
    }
  };

  tick();
  const countdownTimer = window.setInterval(tick, 1000);
  const statusTimer = window.setInterval(refresh, 3000);
  window.addEventListener('pagehide', () => {
    window.clearInterval(countdownTimer);
    window.clearInterval(statusTimer);
  }, {once: true});
})();
