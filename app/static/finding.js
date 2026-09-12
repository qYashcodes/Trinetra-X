(() => {
  const root = document.querySelector('.finding-app');
  if (!root) return;
  const checks = [...root.querySelectorAll('.finding-review-check')];
  const lock = root.querySelector('[data-review-jump]');
  const prepare = root.querySelector('[data-prepare-notice]');
  const action = root.querySelector('.finding-review-action');
  const readyCopy = root.querySelector('[data-ready-copy]');

  const sync = () => {
    const blocked = checks.filter((box) => !box.checked).length;
    prepare.disabled = blocked !== 0;
    action.classList.toggle('ready', blocked === 0);
    lock.classList.toggle('ready', blocked === 0);
    lock.innerHTML = blocked
      ? `Locked: <span data-blocked-count>${blocked}</span> ${blocked === 1 ? 'check' : 'checks'} remaining`
      : 'Prepare freeze notice';
    readyCopy.textContent = blocked
      ? 'The notice cannot be drafted until every confirmation above is recorded against your name.'
      : 'Every confirmation is recorded. The notice is ready to prepare.';
  };
  checks.forEach((box) => box.addEventListener('change', sync));
  lock.addEventListener('click', () => {
    if (prepare.disabled) document.querySelector('#pre-notice-review')?.scrollIntoView({behavior:'smooth', block:'start'});
    else prepare.click();
  });
  sync();

  const copy = root.querySelector('[data-copy-address]');
  copy?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(copy.dataset.address); } catch (_) {}
    copy.classList.add('copied');
    root.querySelector('[data-copy-hint]').textContent = 'Copied. The address is quoted verbatim in the notice.';
    window.setTimeout(() => copy.classList.remove('copied'), 1500);
  });
})();
