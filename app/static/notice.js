(() => {
  const root = document.querySelector('.notice-app');
  if (!root) return;
  const channels = [...root.querySelectorAll('[data-channel]')];
  const periods = [...root.querySelectorAll('input[name="deadline_hours"]')];
  const sendbar = root.querySelector('.notice-sendbar');
  const send = root.querySelector('[data-send]');
  const sendCopy = root.querySelector('[data-send-copy]');
  const live = root.querySelector('[data-live-status]');
  const modal = root.querySelector('[data-confirm-modal]');
  const confirmSend = root.querySelector('[data-confirm-send]');
  const cancelSend = root.querySelector('[data-cancel-send]');
  const signed = root.dataset.countersigned === 'true';
  const canDispatch = root.dataset.canDispatch === 'true';
  const sent = root.dataset.noticeStatus === 'dispatched';
  let modalTrigger = null;

  const selectedCount = () => channels.filter((input) => input.checked).length;
  const periodLabel = () => ({'24':'24 hours','72':'72 hours','168':'7 days'})[periods.find((p) => p.checked)?.value || '24'];

  const syncChannels = () => {
    channels.forEach((input) => {
      const rowState = input.closest('label').querySelector('[data-channel-state]');
      if (!input.checked) { rowState.textContent = 'Not selected'; rowState.style.color = '#5c6880'; }
      else if (!sent) { rowState.textContent = 'Queued'; rowState.style.color = '#5c6880'; }
      // Dispatched channel state comes from the persisted server record, including failures.
    });
    root.querySelector('[data-confirm-channels]').textContent = `${selectedCount()} selected`;
    syncReady();
  };
  const syncReady = () => {
    const ready = selectedCount() > 0 && canDispatch && !sent;
    send.disabled = !ready;
    sendbar.classList.toggle('ready', ready);
    if (sent) return;
    sendCopy.textContent = selectedCount() === 0
      ? 'Select at least one dispatch channel.'
      : !signed
        ? 'Dispatch unlocks once the supervising officer countersigns.'
        : !canDispatch
          ? 'Only the drafting officer may dispatch this notice.'
          : 'Ready to dispatch on the selected channels. You will be asked to confirm.';
  };
  channels.forEach((input) => input.addEventListener('change', syncChannels));

  periods.forEach((input) => input.addEventListener('change', () => {
    const words = {'24':'twenty-four hours','72':'seventy-two hours','168':'seven days'}[input.value];
    root.querySelector('[data-deadline-words]').textContent = words;
    root.querySelector('[data-confirm-period]').textContent = periodLabel();
    root.querySelector('[data-period-note]').textContent = input.value === '24'
      ? 'The document body now reads twenty-four hours, the standing period for a first restraint.'
      : input.value === '72'
        ? 'The document body now reads seventy-two hours, used where the custodian sits outside Indian jurisdiction.'
        : 'The document body now reads seven days. A seven-day period applies only to record production, not to the restraint itself.';
  }));

  const closeModal = () => {
    if (modal.hidden) return;
    modal.hidden = true;
    modalTrigger?.focus();
  };
  send.addEventListener('click', () => {
    if (send.disabled) return;
    modalTrigger = document.activeElement;
    modal.hidden = false;
    confirmSend.focus();
  });
  cancelSend.addEventListener('click', closeModal);
  modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(); });
  document.addEventListener('keydown', (event) => {
    if (modal.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeModal();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [confirmSend, cancelSend];
    const index = focusable.indexOf(document.activeElement);
    if (event.shiftKey && index <= 0) {
      event.preventDefault();
      focusable.at(-1).focus();
    } else if (!event.shiftKey && index === focusable.length - 1) {
      event.preventDefault();
      focusable[0].focus();
    }
  });

  root.querySelector('[data-print]').addEventListener('click', () => window.print());
  root.querySelector('[data-download]').addEventListener('click', () => {
    live.textContent = signed ? 'Signed PDF prepared for download' : 'Draft PDF prepared for download';
  });
  syncChannels();
})();
