(() => {
  const root = document.querySelector('[data-trace-replay]');
  if (!root) return;

  const cadenceMs = 1600;
  const hopShares = root.dataset.hopShares.split('|');
  const hasFinding = Boolean(root.dataset.findingId);
  const states = [
    {
      progress: 0,
      stage: 'Preparing trace',
      run: 'Tracing — hop 1 of 5',
      work: 'expanding outbound set at hop 1 — 3 counterparties',
      addresses: '3', transfers: '11', retained: '—', branches: '0',
    },
    {
      progress: 20,
      stage: 'Following hops',
      run: 'Tracing — hop 2 of 5',
      work: 'scoring 11 outbound transfers at hop 2',
      addresses: '10', transfers: '37', retained: hopShares[0], branches: '2',
    },
    {
      progress: 40,
      stage: 'Following hops',
      run: 'Tracing — hop 3 of 5',
      work: 'resolving peel chain remainder at hop 3',
      addresses: '17', transfers: '63', retained: hopShares[1], branches: '4',
    },
    {
      progress: 60,
      stage: 'Evaluating branches',
      run: 'Tracing — hop 4 of 5',
      work: 'matching hop 4 cluster against attribution set',
      addresses: '24', transfers: '89', retained: hopShares[2], branches: '6',
    },
    {
      progress: 80,
      stage: 'Classifying terminal',
      run: 'Tracing — hop 5 of 5',
      work: hasFinding ? 'confirming deposit-address ownership at hop 5' : 'closing trace boundary',
      addresses: '31', transfers: '115', retained: hopShares[3], branches: '8',
    },
    {
      progress: 100,
      stage: 'Building report',
      run: 'Trace complete',
      work: '',
      addresses: '38', transfers: '141', retained: hopShares[4], branches: '10',
    },
  ];

  const els = {
    runState: root.querySelector('[data-run-state]'),
    heading: root.querySelector('[data-trace-heading]'),
    subheading: root.querySelector('[data-trace-subheading]'),
    workHeading: root.querySelector('[data-work-heading]'),
    workLine: root.querySelector('[data-work-line]'),
    workRow: root.querySelector('.trace-work-row'),
    progressStage: root.querySelector('[data-progress-stage]'),
    track: root.querySelector('.trace-progress-track'),
    scanner: root.querySelector('.trace-progress-scan'),
    elapsed: root.querySelector('[data-elapsed]'),
    addresses: root.querySelector('[data-stat-addresses]'),
    transfers: root.querySelector('[data-stat-transfers]'),
    retained: root.querySelector('[data-stat-retained]'),
    branches: root.querySelector('[data-stat-branches]'),
    candidateMode: root.querySelector('[data-candidate-mode]'),
    advisory: root.querySelector('[data-advisory-dot]'),
    outcome: root.querySelector('[data-outcome-copy]'),
    findingAction: root.querySelector('[data-finding-action]'),
    pause: root.querySelector('[data-trace-pause]'),
    restart: root.querySelector('[data-trace-restart]'),
    dialog: root.querySelector('[data-restart-dialog]'),
  };
  const rows = [...root.querySelectorAll('[data-trace-row]')];
  const candidates = {
    coinsphere: root.querySelector('[data-candidate="coinsphere"]'),
    cluster: root.querySelector('[data-candidate="cluster"]'),
    self: root.querySelector('[data-candidate="self"]'),
  };

  if (root.dataset.terminalOnly === 'true') {
    root.dataset.step = '0';
    root.dataset.state = 'complete';
    root.style.setProperty('--trace-progress', '100%');
    els.runState.querySelector('span').textContent = 'Trace closed';
    els.workHeading.textContent = 'Trace closed';
    els.heading.textContent = root.dataset.terminalKind?.replaceAll('_', ' ') || 'Trace closed';
    els.subheading.textContent = root.dataset.terminalNote || 'No custody finding was created for this seed.';
    els.progressStage.textContent = 'Trace closed';
    els.workLine.textContent = '';
    els.workRow.hidden = true;
    els.addresses.textContent = '0';
    els.transfers.textContent = '0';
    els.retained.textContent = '—';
    els.branches.textContent = '0';
    rows.forEach((row) => { row.hidden = false; row.classList.remove('is-active'); });
    Object.values(candidates).forEach((candidate) => {
      if (candidate) candidate.hidden = true;
    });
    els.candidateMode.textContent = 'none';
    els.outcome.textContent = root.dataset.terminalNote || 'Trace closed without custody evidence.';
    els.pause.disabled = true;
    els.pause.textContent = 'Closed';
    if (els.findingAction?.matches('a')) {
      els.findingAction.setAttribute('aria-disabled', 'true');
      els.findingAction.tabIndex = -1;
    }
    return;
  }

  let startedAt = performance.now();
  let pausedAt = null;
  let pausedFor = 0;
  let currentStep = -1;
  let lastSecond = -1;
  let animationFrame = 0;

  const formatElapsed = (seconds) => `00:${String(Math.min(8, seconds)).padStart(2, '0')}`;

  const reveal = (element, shouldShow, animate) => {
    if (!element) return;
    const wasHidden = element.hidden;
    element.hidden = !shouldShow;
    if (shouldShow && wasHidden && animate) {
      element.classList.remove('is-entering');
      void element.offsetWidth;
      element.classList.add('is-entering');
      window.setTimeout(() => element.classList.remove('is-entering'), 260);
    } else if (!shouldShow) {
      element.classList.remove('is-entering');
    }
  };

  const restartScanner = () => {
    if (!els.scanner || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    els.scanner.style.animation = 'none';
    void els.scanner.offsetWidth;
    els.scanner.style.animation = '';
  };

  const renderStep = (step, animate = true) => {
    const state = states[step];
    const complete = step === states.length - 1;
    currentStep = step;
    root.dataset.step = String(step);
    root.dataset.state = complete ? 'complete' : (pausedAt === null ? 'running' : 'paused');
    root.style.setProperty('--trace-progress', `${state.progress}%`);

    els.runState.querySelector('span').textContent = state.run;
    els.workHeading.textContent = state.run;
    els.heading.textContent = complete ? 'Traversal closed at hop 4' : 'Following the funds';
    els.subheading.textContent = complete
      ? (hasFinding
        ? 'The largest surviving share reached an address held by a registered exchange.'
        : (root.dataset.terminalNote || 'Trace closed without custody evidence.'))
      : 'Each hop is written to the case record as it resolves. The graph builds in parallel.';
    els.progressStage.textContent = complete ? 'Trace complete' : state.stage;
    els.workLine.textContent = state.work;
    els.workRow.hidden = complete;

    els.addresses.textContent = state.addresses;
    els.transfers.textContent = state.transfers;
    els.retained.textContent = state.retained;
    els.branches.textContent = state.branches;

    rows.forEach((row) => {
      const rowStep = Number(row.dataset.traceRow);
      reveal(row, rowStep <= step, animate);
      row.classList.toggle('is-active', !complete && rowStep === step);
    });
    reveal(candidates.cluster, hasFinding && step >= 3, animate);
    reveal(candidates.self, hasFinding && step >= 4, animate);
    reveal(candidates.coinsphere, hasFinding && complete, animate);
    els.candidateMode.textContent = hasFinding ? (complete ? 'ranked' : 'provisional') : 'none';

    els.advisory.classList.toggle('amber', !complete);
    els.advisory.classList.toggle('green', complete);
    els.outcome.textContent = complete
      ? (hasFinding
        ? `${root.dataset.terminalAmount} of the reported amount sits at a deposit address attributed to ${root.dataset.custodianName}. A freeze notice can be raised from the finding.`
        : (root.dataset.terminalNote || 'Trace closed without custody evidence.'))
      : 'Findings are provisional until the traversal closes. Nothing is dispatched from this screen.';

    if (els.findingAction?.matches('a')) {
      els.findingAction.setAttribute('aria-disabled', complete ? 'false' : 'true');
      els.findingAction.tabIndex = complete ? 0 : -1;
    }
    els.pause.disabled = complete;
    els.pause.textContent = complete ? 'Completed' : (pausedAt === null ? 'Pause' : 'Resume');
    if (!complete) restartScanner();
  };

  const activeElapsed = (now) => {
    const end = pausedAt === null ? now : pausedAt;
    return Math.max(0, end - startedAt - pausedFor);
  };

  const tick = (now) => {
    const elapsedMs = activeElapsed(now);
    const targetStep = Math.min(states.length - 1, Math.floor(elapsedMs / cadenceMs));
    if (targetStep !== currentStep) renderStep(targetStep, currentStep !== -1);
    const seconds = Math.min(8, Math.floor(elapsedMs / 1000));
    if (seconds !== lastSecond) {
      lastSecond = seconds;
      els.elapsed.textContent = formatElapsed(seconds);
    }
    animationFrame = window.requestAnimationFrame(tick);
  };

  const resetReplay = () => {
    pausedAt = null;
    pausedFor = 0;
    currentStep = -1;
    lastSecond = -1;
    startedAt = performance.now();
    rows.forEach((row) => { row.hidden = true; row.classList.remove('is-active', 'is-entering'); });
    Object.values(candidates).forEach((candidate) => {
      if (candidate) { candidate.hidden = true; candidate.classList.remove('is-entering'); }
    });
    renderStep(0, false);
    els.elapsed.textContent = '00:00';
  };

  els.pause.addEventListener('click', () => {
    if (currentStep === states.length - 1) return;
    if (pausedAt === null) {
      pausedAt = performance.now();
      root.dataset.state = 'paused';
      els.pause.textContent = 'Resume';
      els.runState.querySelector('span').textContent = states[currentStep].run.replace('Tracing', 'Paused');
    } else {
      pausedFor += performance.now() - pausedAt;
      pausedAt = null;
      root.dataset.state = 'running';
      els.pause.textContent = 'Pause';
      els.runState.querySelector('span').textContent = states[currentStep].run;
      restartScanner();
    }
  });

  els.restart.addEventListener('click', () => {
    if (typeof els.dialog.showModal === 'function') els.dialog.showModal();
    else if (window.confirm('Restart this trace? The current evidence stays recorded.')) resetReplay();
  });

  els.dialog?.addEventListener('close', () => {
    if (els.dialog.returnValue === 'restart') resetReplay();
  });

  els.findingAction?.addEventListener('click', (event) => {
    if (els.findingAction.getAttribute('aria-disabled') === 'true') event.preventDefault();
  });

  renderStep(0, false);
  animationFrame = window.requestAnimationFrame(tick);
  window.addEventListener('pagehide', () => window.cancelAnimationFrame(animationFrame), {once: true});
})();
