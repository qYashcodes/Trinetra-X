(() => {
  "use strict";

  const root = document.querySelector("[data-canvas-workspace]");
  if (!root) return;

  const evidence = JSON.parse(root.querySelector("[data-canvas-evidence]").textContent);
  const path = evidence.dominant_path;
  const asset = evidence.case.asset;
  const fixtureAddresses = Object.freeze({
    deposit: root.dataset.depositAddress,
    hotWallet: root.dataset.hotWalletAddress
  });
  const shortenAddress = (address, prefixLength, suffixLength = 6) =>
    `${address.slice(0, prefixLength)}...${address.slice(-suffixLength)}`;
  const formatAmount = (amountBase) =>
    `${(Number(amountBase) / (10 ** asset.decimals)).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})} ${asset.symbol}`;
  const formatIst = (timestampMs) => {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }).formatToParts(new Date(timestampMs)).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} IST`;
  };
  const shortHash = (hash) => shortenAddress(hash, 6, 4);
  const parkedValue = evidence.parked.reduce((total, branch) => total + Number(branch.value_base), 0);

  const nodes = [
    {
      id: "h0", title: "Victim payment recipient", chip: "Reported", tone: "slate",
      subtitle: "Address recorded as the recipient in the filed complaint", address: shortenAddress(path[0].address, 13, 7), fullAddress: path[0].address,
      classification: "Unclassified address", confidence: `${Math.round(path[0].confidence * 100)}%`, received: formatAmount(path[0].value_base),
      onward: formatAmount(path[1].value_base), firstSeen: formatIst(path[0].ts_ms), inbound: "31", hash: shortHash(path[0].txids[0]),
      whyTitle: "Why TRINETRA flagged this address",
      why: ["Named in the NCRP complaint", "Full balance swept in 12 minutes", "No service attribution found", "Single onward destination"],
      entity: [["Name", "Unknown"], ["Type", "Reported recipient address"], ["Jurisdiction", "Unknown"], ["Status", "Empty"], ["Source", "Complaint intake"], ["Last updated", "2026-08-30"]]
    },
    {
      id: "h1", title: "Pass-through wallet", chip: "Cluster", tone: "slate",
      subtitle: "Intermediate hop with a single inbound and outbound transfer", address: shortenAddress(path[1].address, 13, 7), fullAddress: path[1].address,
      classification: "Unclassified hop", confidence: `${Math.round(path[1].confidence * 100)}%`, received: formatAmount(path[1].value_base),
      onward: formatAmount(path[2].value_base), firstSeen: formatIst(path[1].ts_ms), inbound: "2", hash: shortHash(path[1].txids[0]),
      whyTitle: "Why TRINETRA treats this as a hop",
      why: ["One inbound, one outbound", "Balance returned to near zero", "Sweep timing 3m 51s", "No registry match"],
      entity: [["Name", "Unknown"], ["Type", "Pass-through cluster"], ["Jurisdiction", "Unknown"], ["Status", "Empty"], ["Source", "TRINETRA clustering"], ["Last updated", "2026-08-30"]]
    },
    {
      id: "h2", title: "Peel chain wallet", chip: "Peel chain", tone: "amber",
      subtitle: "Splits value, with one branch parked outside the dominant flow", address: shortenAddress(path[2].address, 13, 7), fullAddress: path[2].address,
      classification: "Peel-chain wallet", confidence: `${Math.round(path[2].confidence * 100)}%`, received: formatAmount(path[2].value_base),
      onward: `${formatAmount(path[3].value_base)} (${(path[3].share_bp / 100).toFixed(2)}%)`, firstSeen: formatIst(path[2].ts_ms), inbound: "5", hash: shortHash(path[2].txids[0]),
      whyTitle: "Why TRINETRA calls this a peel chain",
      why: [`${formatAmount(parkedValue)} parked outside the dominant flow`, "Largest share forwarded onward", "Parked branches retained in the case record", "No custody attribution at this hop"],
      entity: [["Name", "Unknown"], ["Type", "Peel-chain cluster"], ["Jurisdiction", "Unknown"], ["Status", "Empty"], ["Source", "TRINETRA clustering"], ["Last updated", "2026-08-30"]]
    },
    {
      id: "h3", title: "Consolidation wallet", chip: "Cluster", tone: "slate",
      subtitle: "Forwards the surviving tranche to a single deposit address", address: shortenAddress(path[3].address, 13, 7), fullAddress: path[3].address,
      classification: "Consolidation hop", confidence: `${Math.round(path[3].confidence * 100)}%`, received: formatAmount(path[3].value_base),
      onward: formatAmount(path[4].value_base), firstSeen: formatIst(path[3].ts_ms), inbound: "5", hash: shortHash(path[3].txids[0]),
      whyTitle: "Why TRINETRA treats this as a hop",
      why: ["Single onward destination", "Destination feeds one exchange hot wallet", "Forward ratio 0.963", "Sweep timing 6m 42s"],
      entity: [["Name", "Unknown"], ["Type", "Consolidation cluster"], ["Jurisdiction", "Unknown"], ["Status", "Empty"], ["Source", "TRINETRA clustering"], ["Last updated", "2026-08-30"]]
    },
    {
      id: "h4", title: "Exchange deposit address", chip: "Probable deposit", tone: "amber",
      subtitle: "Custodial deposit address, funds at rest behind the exchange",
      address: shortenAddress(fixtureAddresses.deposit, 4), fullAddress: fixtureAddresses.deposit,
      addressRole: "Exchange deposit address",
      classification: "Probable deposit address", confidence: `${Math.round(path[4].confidence * 100)}%`, received: formatAmount(path[4].value_base),
      onward: `${formatAmount(evidence.terminal.amount_credited_base)} at rest`, firstSeen: formatIst(path[4].ts_ms), inbound: "187", hash: shortHash(path[4].txids[0]),
      whyTitle: "Why TRINETRA thinks this is a deposit address",
      why: ["187 of 187 outbounds to one hot wallet", "Balance swept to zero each time", "Fan-in from 187 sources", "Median sweep 4 minutes"],
      entity: [["Name", evidence.entity.name], ["Type", "Centralized Exchange (VASP)"], ["Jurisdiction", evidence.entity.jurisdiction], ["Status", "Active"], ["Source", "TRINETRA VASP dataset"], ["Last updated", "2026-08-24"]]
    },
    {
      id: "ex", title: "Coinsphere hot wallet", chip: "Verified VASP", tone: "green",
      subtitle: "Registered VASP, Coinsphere Global Pte Ltd",
      address: shortenAddress(fixtureAddresses.hotWallet, 13), fullAddress: fixtureAddresses.hotWallet,
      addressRole: "Custodian hot-wallet address",
      classification: "Verified VASP hot wallet", confidence: "94%", received: formatAmount(evidence.terminal.amount_credited_base),
      onward: "Operational (exchange)", firstSeen: formatIst(evidence.hot_wallet.ts_ms), inbound: "2", hash: shortHash(evidence.hot_wallet.txid),
      whyTitle: "Why TRINETRA thinks this is Coinsphere",
      why: ["Address found in VASP registry dataset", "Matches known Coinsphere address cluster", "Consistent transaction behaviour", "Historical aggregation patterns", "94% confidence score"],
      entity: [["Name", evidence.entity.name], ["Type", "Centralized Exchange (VASP)"], ["Jurisdiction", evidence.entity.jurisdiction], ["Status", "Active"], ["Source", "TRINETRA VASP dataset"], ["Last updated", "2026-08-24"]]
    }
  ];

  const state = { selected: "ex", panelTab: "overview", zoom: 1, copiedTimer: null };
  const detailContent = root.querySelector("[data-detail-content]");
  const live = root.querySelector("[data-canvas-live]");
  const graphViewport = root.querySelector(".canvas-graph-viewport");
  const flowScale = root.querySelector("[data-flow-scale]");
  const flowContent = root.querySelector("[data-flow-content]");

  const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const currentNode = () => nodes.find((node) => node.id === state.selected) || nodes[nodes.length - 1];
  const say = (message) => { if (live) live.textContent = message; };

  function keyValueRows(node) {
    const rows = [
      ["Classification", node.classification, false],
      ["Confidence", node.confidence, false],
      ["Received", node.received, false],
      ["Onward", node.onward, false],
      ["First seen", node.firstSeen, false],
      ["Inbound txs", node.inbound, false],
      ["Inbound hash", node.hash, true]
    ];
    return `<div class="detail-kv">${rows.map(([key, value, mono]) =>
      `<div class="detail-kv-row"><span>${escapeHtml(key)}</span><b class="${mono ? "mono" : ""}">${escapeHtml(value)}</b></div>`
    ).join("")}</div>`;
  }

  function renderOverview(node) {
    const why = node.why.map((item) => `<span><i aria-hidden="true">✓</i><b>${escapeHtml(item)}</b></span>`).join("");
    const entity = node.entity.map(([key, value]) => `<span><small>${escapeHtml(key)}</small><b>${escapeHtml(value)}</b></span>`).join("");
    return `${keyValueRows(node)}
      <button class="detail-primary" type="button" data-open-transactions><span>View transactions</span><span aria-hidden="true">→</span></button>
      <a class="detail-primary" href="/findings/1"><span>Review custody findings</span><span aria-hidden="true">→</span></a>
      <div class="detail-why"><strong>${escapeHtml(node.whyTitle)}</strong>${why}</div>
      <div class="detail-actions">
        <button type="button"><i aria-hidden="true">⊕</i><span>Expand onward</span></button>
        <button type="button"><i aria-hidden="true">↗</i><span>View in explorer</span></button>
        <button type="button"><i aria-hidden="true">⚑</i><span>Mark as custody point</span></button>
        <button type="button"><i aria-hidden="true">✎</i><span>Add note</span></button>
      </div>
      <div class="detail-entity"><strong>Entity information</strong>${entity}</div>`;
  }

  function renderTransactions(node) {
    return `<div class="detail-transaction"><strong>Inbound transaction 1</strong><p class="mono">${escapeHtml(node.hash)}</p><p>${escapeHtml(node.received)} · ${escapeHtml(node.firstSeen)}</p></div>
      <div class="detail-transaction"><strong>Inbound transaction 2</strong><p class="mono">72cd16...b08e</p><p>Corroborating transfer into the same attributed wallet.</p></div>
      <button class="detail-primary" type="button"><span>Open transaction view</span><span aria-hidden="true">→</span></button>`;
  }

  function renderAttribution(node) {
    return `<div class="detail-why"><strong>${escapeHtml(node.whyTitle)}</strong>${node.why.map((item) => `<span><i aria-hidden="true">✓</i><b>${escapeHtml(item)}</b></span>`).join("")}</div>
      <div class="detail-empty"><strong>Attribution set</strong><p>TRINETRA VASP dataset · revision 2026-08-24</p></div>`;
  }

  function renderNotes() {
    return `<div class="detail-empty"><strong>Investigator notes</strong><p>Notes are retained in the local case record and do not alter the source transaction data.</p></div>
      <label class="detail-empty"><strong>Add note</strong><textarea class="detail-note" placeholder="Record an investigative observation"></textarea></label>`;
  }

  function renderDetailBody() {
    const node = currentNode();
    const markup = state.panelTab === "transactions" ? renderTransactions(node)
      : state.panelTab === "attribution" ? renderAttribution(node)
      : state.panelTab === "notes" ? renderNotes()
      : renderOverview(node);
    detailContent.innerHTML = markup;
    detailContent.scrollTop = 0;
    detailContent.querySelector("[data-open-transactions]")?.addEventListener("click", () => activatePanelTab("transactions"));
  }

  function renderSelection(announce = false) {
    const node = currentNode();
    const index = nodes.indexOf(node);

    root.querySelectorAll("[data-node-id]").forEach((element) => {
      element.setAttribute("aria-pressed", String(element.dataset.nodeId === node.id));
    });
    root.querySelector("[data-hop-count]").textContent = `Hop ${index} of ${nodes.length - 1}`;
    root.querySelector("[data-detail-title]").textContent = node.title;
    root.querySelector("[data-detail-subtitle]").textContent = node.subtitle;
    const address = root.querySelector("[data-detail-address]");
    const fullAddress = node.fullAddress || node.address;
    const addressRole = node.addressRole || "Blockchain address";
    address.textContent = node.address;
    address.title = fullAddress;
    address.setAttribute("aria-label", `${addressRole}: ${fullAddress}`);
    root.querySelector("[data-copy-address]").setAttribute("aria-label", `Copy full ${addressRole.toLowerCase()} ${fullAddress}`);

    const chip = root.querySelector("[data-detail-chip]");
    chip.className = `detail-chip canvas-chip-${node.tone}`;
    chip.querySelector("b").textContent = node.chip;

    const previous = root.querySelector("[data-step='previous']");
    const next = root.querySelector("[data-step='next']");
    previous.disabled = index === 0;
    next.disabled = index === nodes.length - 1;
    renderDetailBody();
    if (announce) say(`${node.title} selected, hop ${index} of ${nodes.length - 1}.`);
  }

  function selectNode(id, announce = true) {
    if (!nodes.some((node) => node.id === id)) return;
    state.selected = id;
    renderSelection(announce);
  }

  function activatePanelTab(tab) {
    state.panelTab = tab;
    root.querySelectorAll("[data-panel-tab]").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.panelTab === tab));
    });
    renderDetailBody();
    say(`${root.querySelector(`[data-panel-tab='${tab}']`).textContent.trim()} panel opened.`);
  }

  root.querySelectorAll("[data-node-id]").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.nodeId));
  });

  root.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = nodes.findIndex((node) => node.id === state.selected);
      const direction = button.dataset.step === "previous" ? -1 : 1;
      const next = Math.max(0, Math.min(nodes.length - 1, index + direction));
      selectNode(nodes[next].id);
    });
  });

  root.querySelector("[data-panel-close]").addEventListener("click", () => {
    state.panelTab = "overview";
    root.querySelectorAll("[data-panel-tab]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.panelTab === "overview")));
    selectNode("ex");
  });

  root.querySelectorAll("[data-panel-tab]").forEach((button) => {
    button.addEventListener("click", () => activatePanelTab(button.dataset.panelTab));
  });

  root.querySelector("[data-copy-address]").addEventListener("click", (event) => {
    const node = currentNode();
    const fullAddress = node.fullAddress || node.address;
    event.currentTarget.textContent = "Copied";
    event.currentTarget.style.color = "#16a34a";
    if (navigator.clipboard) navigator.clipboard.writeText(fullAddress).catch(() => {});
    clearTimeout(state.copiedTimer);
    state.copiedTimer = window.setTimeout(() => {
      event.currentTarget.textContent = "Copy";
      event.currentTarget.style.color = "";
    }, 1400);
    say(`${node.addressRole || "Blockchain address"} copied.`);
  });

  root.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const enabled = button.getAttribute("aria-checked") !== "true";
      button.setAttribute("aria-checked", String(enabled));
      if (button.dataset.toggle === "values") root.classList.toggle("values-hidden", !enabled);
      if (button.dataset.toggle === "parked") root.classList.toggle("parked-hidden", !enabled);
      resizeFlow();
      say(`${button.getAttribute("aria-label")} ${enabled ? "shown" : "hidden"}.`);
    });
  });

  const parkedButton = root.querySelector("[data-parked-expand]");
  const parkedList = root.querySelector("[data-parked-list]");
  parkedButton.addEventListener("click", () => {
    const open = parkedButton.getAttribute("aria-expanded") !== "true";
    parkedButton.setAttribute("aria-expanded", String(open));
    parkedButton.setAttribute("aria-label", `${open ? "Collapse" : "Expand"} 2 parked branches`);
    parkedButton.textContent = open ? "▲" : "▼";
    parkedList.classList.toggle("open", open);
    window.setTimeout(resizeFlow, 220);
    say(`Parked branches ${open ? "expanded" : "collapsed"}.`);
  });

  function resizeFlow() {
    const naturalHeight = Math.max(286, flowContent.scrollHeight);
    flowScale.style.width = `${Math.round(1244 * state.zoom)}px`;
    flowScale.style.height = `${Math.round(naturalHeight * state.zoom)}px`;
    flowContent.style.transform = `scale(${state.zoom})`;
  }

  function setZoom(nextZoom, label) {
    const previous = state.zoom;
    state.zoom = Math.max(.62, Math.min(1.35, nextZoom));
    const centerX = graphViewport.scrollLeft + graphViewport.clientWidth / 2;
    const centerY = graphViewport.scrollTop + graphViewport.clientHeight / 2;
    resizeFlow();
    const ratio = state.zoom / previous;
    graphViewport.scrollTo({
      left: Math.max(0, centerX * ratio - graphViewport.clientWidth / 2),
      top: Math.max(0, centerY * ratio - graphViewport.clientHeight / 2),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
    });
    say(`${label}. Graph zoom ${Math.round(state.zoom * 100)} percent.`);
  }

  root.querySelectorAll("[data-zoom]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.zoom === "in") setZoom(state.zoom + .1, "Zoomed in");
      else if (button.dataset.zoom === "out") setZoom(state.zoom - .1, "Zoomed out");
      else {
        const fit = Math.min(1, (graphViewport.clientWidth - 8) / 1244);
        setZoom(fit, "Graph fitted to view");
        graphViewport.scrollTo({ left: 0, top: 0, behavior: "smooth" });
      }
    });
  });

  root.querySelectorAll("[data-bottom-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.bottomTab;
      root.querySelectorAll("[data-bottom-tab]").forEach((item) => item.setAttribute("aria-selected", String(item.dataset.bottomTab === tab)));
      root.querySelectorAll("[data-bottom-panel]").forEach((panel) => { panel.hidden = panel.dataset.bottomPanel !== tab; });
      say(`${button.textContent.trim()} opened.`);
    });
  });

  renderSelection(false);
  resizeFlow();
})();
