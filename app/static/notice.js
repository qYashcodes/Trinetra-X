(() => {
  const root = document.querySelector('.notice-app');
  if (!root) return;
  const channels = [...root.querySelectorAll('[data-channel]')];
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

  root.querySelector('[data-print]')?.addEventListener('click', () => window.print());
  root.querySelector('[data-download]')?.addEventListener('click', () => {
    live.textContent = signed ? 'Signed PDF prepared for download' : 'Draft PDF prepared for download';
  });

  syncChannels();
})();
