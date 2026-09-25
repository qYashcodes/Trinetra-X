(() => {
  "use strict";

  const root = document.querySelector("[data-trace-replay]");
  if (!root) return;

  const traceReplayDelayMs = 1400;
  const replayStartedAt = Date.now();
  const replayEventTypes = new Set(["hop", "parked", "stats", "candidate", "terminal", "done", "failed"]);
  const isFixtureTrace = root.dataset.traceMode === "fixture";
  const finalStats = {
    addresses: Number(root.dataset.finalAddresses || 0),
    transfers: Number(root.dataset.finalTransfers || 0),
    branches: Number(root.dataset.finalBranches || 0),
    retained: root.dataset.finalRetained || "-",
    durationSeconds: Number(root.dataset.finalDurationSeconds || 0),
  };
  const state = {
    resolved: 0,
    hops: 0,
    parked: 0,
    terminal: null,
    complete: false,
    failed: false,
    lastEventId: 0,
  };
  const els = {
    runState: root.querySelector("[data-run-state] span"),
    heading: root.querySelector("[data-trace-heading]"),
    subheading: root.querySelector("[data-trace-subheading]"),
    workHeading: root.querySelector("[data-work-heading]"),
    workLine: root.querySelector("[data-work-line]"),
    workTableLine: root.querySelector("[data-work-table-line]"),
    workRow: root.querySelector(".trace-work-row"),
    elapsed: root.querySelector("[data-elapsed]"),
    addresses: root.querySelector("[data-stat-addresses]"),
    transfers: root.querySelector("[data-stat-transfers]"),
    retained: root.querySelector("[data-stat-retained]"),
    branches: root.querySelector("[data-stat-branches]"),
    candidateMode: root.querySelector("[data-candidate-mode]"),
    advisory: root.querySelector("[data-advisory-dot]"),
    outcome: root.querySelector("[data-outcome-copy]"),
    canvasAction: root.querySelector("[data-canvas-action]"),
    findingAction: root.querySelector("[data-finding-action]"),
    pause: root.querySelector("[data-trace-pause]"),
    restart: root.querySelector("[data-trace-restart]"),
    dialog: root.querySelector("[data-restart-dialog]"),
  };
  const rows = [...root.querySelectorAll("[data-trace-row]")];
  const candidates = {
    coinsphere: root.querySelector('[data-candidate="coinsphere"]'),
    cluster: root.querySelector('[data-candidate="cluster"]'),
    self: root.querySelector('[data-candidate="self"]'),
  };
  const eventQueue = [];
  let drainingQueue = false;

  if (els.pause) {
    els.pause.disabled = true;
    els.pause.textContent = "Provider controlled";
    els.pause.title = "Traversal pauses only when the provider reports backoff.";
  }
  rows.forEach((row) => { row.hidden = true; });
  Object.values(candidates).forEach((candidate) => {
    if (candidate) candidate.hidden = true;
  });

  const setStatus = (label) => {
    if (els.runState) els.runState.textContent = label;
    if (els.workHeading) els.workHeading.textContent = label;
  };

  const setWorkLine = (text) => {
    if (els.workLine) els.workLine.textContent = text;
    if (els.workTableLine) els.workTableLine.textContent = text;
  };

  const setElapsed = (closedTs = null) => {
    const started = Number(root.dataset.startedTs || 0);
    const closed = Number(closedTs || root.dataset.closedTs || 0);
    const hasRecordedClose = closed > 0;
    let seconds = 0;
    if (state.complete && finalStats.durationSeconds > 0) {
      seconds = finalStats.durationSeconds;
    } else if (hasRecordedClose && finalStats.durationSeconds > 0) {
      seconds = Math.min(
        finalStats.durationSeconds,
        Math.max(0, Math.floor((Date.now() - replayStartedAt) / 1000)),
      );
    } else if (started) {
      const ended = Number(closedTs || Date.now());
      seconds = Math.max(0, Math.floor((ended - started) / 1000));
    }
    if (!els.elapsed) return;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    els.elapsed.textContent = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  };

  const renderRuntime = (runtime) => {
    const rawStatus = String(runtime?.status || runtime?.snapshot_status || "").toLowerCase();
    const paused = Boolean(runtime?.provider_backoff?.active) || rawStatus.includes("pause") || rawStatus.includes("backoff");
    if (paused && !state.complete) {
      root.dataset.state = "paused";
      const retryMs = Number(runtime?.provider_backoff?.retry_at_ms || 0);
      const seconds = retryMs ? Math.max(0, Math.ceil((retryMs - Date.now()) / 1000)) : null;
      const label = seconds === null ? "Paused - provider rate limit" : `Paused - provider rate limit (${seconds}s)`;
      setStatus(label);
      return;
    }
    if (!state.complete) {
      root.dataset.state = "running";
      setStatus("Trace under effect");
    }
  };

  const revealRow = (index) => {
    const row = rows[index];
    if (!row) return;
    row.hidden = false;
    row.classList.add("is-active");
    row.classList.add("is-entering");
    rows.forEach((item) => {
      if (item !== row) item.classList.remove("is-active");
    });
  };

  const consume = (type, data, id) => {
    const numericId = Number(id || 0);
    if (numericId && numericId <= state.lastEventId) return;
    if (numericId) state.lastEventId = numericId;
    state.resolved += 1;

    if (type === "hop") {
      revealRow(state.hops);
      state.hops += 1;
      setStatus("Trace under effect");
      setWorkLine(`Recorded hop ${data.hop ?? state.hops - 1} at ${shortAddress(data.address)}`);
      if (els.addresses) els.addresses.textContent = String(state.hops);
      if (els.retained && data.share_bp !== undefined && data.share_bp !== null) {
        els.retained.textContent = `${(Number(data.share_bp) / 100).toFixed(2)}%`;
      }
    } else if (type === "parked") {
      state.parked += 1;
      if (els.branches) els.branches.textContent = String(state.parked);
      setWorkLine(`Parked branch ${state.parked} below expansion threshold`);
      const candidate = state.parked === 1 ? candidates.cluster : candidates.self;
      if (candidate) candidate.hidden = false;
    } else if (type === "stats") {
      if (els.addresses && data.addresses_visited !== undefined) {
        els.addresses.textContent = String(data.addresses_visited);
      }
      if (els.transfers && data.transfers_read !== undefined) {
        els.transfers.textContent = String(isFixtureTrace ? (data.addresses_visited ?? state.hops) : data.transfers_read);
      }
      if (els.retained && data.retained_share_bp !== undefined && data.retained_share_bp !== null) {
        els.retained.textContent = formatPctBp(data.retained_share_bp);
      }
      if (els.branches && data.branches_parked !== undefined) {
        els.branches.textContent = String(data.branches_parked);
      }
    } else if (type === "candidate") {
      if (els.candidateMode) els.candidateMode.textContent = "observed";
    } else if (type === "terminal") {
      state.terminal = data;
      if (root.dataset.terminalOnly === "true" && state.hops === 0) revealRow(0);
      if (candidates.coinsphere && root.dataset.findingId) candidates.coinsphere.hidden = false;
      if (els.outcome) els.outcome.textContent = data.note || "Trace reached its recorded evidence boundary.";
      setWorkLine("Terminal evidence boundary recorded");
    } else if (type === "done") {
      completeTrace(data);
    } else if (type === "failed") {
      failTrace(data);
    }
  };

  const completeTrace = (data) => {
    state.complete = true;
    root.dataset.state = "complete";
    rows.forEach((row) => { row.classList.remove("is-active"); });
    setStatus("Trace completed");
    if (els.heading) els.heading.textContent = "Trace completed";
    if (els.subheading) els.subheading.textContent = state.terminal?.note || root.dataset.terminalNote || "Trace completed.";
    setWorkLine("Trace completed");
    if (els.workRow) els.workRow.hidden = true;
    if (els.addresses) els.addresses.textContent = String(finalStats.addresses);
    if (els.transfers) els.transfers.textContent = String(finalStats.transfers);
    if (els.retained) els.retained.textContent = finalStats.retained;
    if (els.branches) els.branches.textContent = String(finalStats.branches);
    if (els.candidateMode) els.candidateMode.textContent = root.dataset.findingId ? "recorded" : "none";
    if (els.canvasAction?.matches("a")) {
      els.canvasAction.setAttribute("aria-disabled", "false");
      els.canvasAction.tabIndex = 0;
    }
    setElapsed(data?.closed_ts);
  };

  const failTrace = (data) => {
    state.complete = true;
    state.failed = true;
    root.dataset.state = "failed";
    setStatus("Trace failed");
    if (els.heading) els.heading.textContent = "Trace stopped at an evidence boundary";
    if (els.subheading) els.subheading.textContent = data?.reason || root.dataset.terminalNote || "The trace did not produce a custody finding.";
    setWorkLine("Trace stopped");
    if (els.workRow) els.workRow.hidden = true;
    setElapsed(data?.closed_ts);
  };

  const enqueueEvent = (type, data, id) => {
    if (!replayEventTypes.has(type)) return;
    eventQueue.push({ type, data, id });
    drainQueue();
  };

  const drainQueue = () => {
    if (drainingQueue || eventQueue.length === 0) return;
    drainingQueue = true;
    const next = eventQueue.shift();
    consume(next.type, next.data, next.id);
    window.setTimeout(() => {
      drainingQueue = false;
      drainQueue();
    }, delayAfter(next.type));
  };

  const openStream = () => {
    if (!root.dataset.streamUrl || typeof EventSource === "undefined") {
      loadJsonEvents();
      return;
    }
    const source = new EventSource(root.dataset.streamUrl);
    for (const eventType of ["hop", "parked", "stats", "dust", "candidate", "terminal", "done", "failed"]) {
      source.addEventListener(eventType, (event) => {
        let data = {};
        try { data = JSON.parse(event.data || "{}"); } catch (_error) { data = {}; }
        enqueueEvent(eventType, data, event.lastEventId);
        if (["done", "failed"].includes(eventType)) source.close();
      });
    }
    source.onerror = () => {
      source.close();
      if (!state.complete) loadJsonEvents();
    };
  };

  const loadJsonEvents = async () => {
    try {
      const response = await fetch(`${root.dataset.streamUrl}?transport=json&after=${state.lastEventId}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
      const events = await response.json();
      events.forEach((event) => enqueueEvent(event.event, event.data || {}, event.id));
    } catch (error) {
      console.error("Trace events unavailable:", error);
      failTrace({ reason: "Persisted trace events could not be loaded." });
    }
  };

  const pollRuntime = async () => {
    if (state.complete || !root.dataset.statusUrl) return;
    try {
      const response = await fetch(root.dataset.statusUrl, { credentials: "same-origin" });
      if (response.ok) renderRuntime(await response.json());
    } catch (_error) {
      // The last verified state remains visible while polling is unavailable.
    }
  };

  els.restart?.addEventListener("click", () => {
    if (typeof els.dialog?.showModal === "function") els.dialog.showModal();
  });
  els.dialog?.querySelector("[data-restart-cancel]")?.addEventListener("click", () => els.dialog.close());
  els.canvasAction?.addEventListener("click", (event) => {
    if (els.canvasAction.getAttribute("aria-disabled") === "true") event.preventDefault();
  });
  els.findingAction?.addEventListener("click", (event) => {
    if (els.findingAction.getAttribute("aria-disabled") === "true") event.preventDefault();
  });

  root.dataset.state = "queued";
  setStatus("Trace under effect");
  setWorkLine("Waiting for first hop");
  setElapsed();
  openStream();
  const runtimeTimer = window.setInterval(pollRuntime, 1000);
  const elapsedTimer = window.setInterval(() => setElapsed(), 1000);
  window.addEventListener("pagehide", () => {
    window.clearInterval(runtimeTimer);
    window.clearInterval(elapsedTimer);
  }, { once: true });

  function shortAddress(value) {
    const text = String(value || "");
    return text.length > 18 ? `${text.slice(0, 8)}...${text.slice(-6)}` : text;
  }

  function formatPctBp(value) {
    const numeric = Number(value || 0);
    return `${(numeric / 100).toFixed(2)}%`;
  }

  function delayAfter(type) {
    if (type === "stats") return traceReplayDelayMs;
    if (type === "hop") return 350;
    return 500;
  }
})();
