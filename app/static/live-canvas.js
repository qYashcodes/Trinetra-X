(() => {
  const NS = "http://www.w3.org/2000/svg";
  const shell = document.querySelector("[data-live-canvas-graph]");
  if (!shell) return;

  const dataNode = shell.querySelector("[data-live-canvas-json]");
  const viewport = shell.querySelector("[data-live-graph-viewport]");
  const svg = shell.querySelector("[data-live-graph-svg]");
  const details = shell.querySelector("[data-live-graph-details]");
  if (!dataNode || !viewport || !svg || !details) return;

  let result = {};
  try {
    result = JSON.parse(dataNode.textContent || "{}");
  } catch (_error) {
    result = {};
  }

  const assetSymbol = shell.dataset.assetSymbol || result.asset?.symbol || "USDT";
  const assetDecimals = Number(shell.dataset.assetDecimals ?? result.asset?.decimals ?? 6);
  const seedAddress =
    shell.dataset.caseAddress ||
    result.case?.reported_address ||
    result.seed?.address ||
    result.seed_match?.address ||
    "Reported address";
  const seedAmountBase =
    numberOrNull(result.case?.amount_reported_base) ??
    numberOrNull(result.seed_match?.amount_base) ??
    numberOrNull(result.terminal?.amount_credited_base) ??
    0;

  const nodeWidth = 184;
  const nodeHeight = 88;
  const depthGap = 246;
  const branchGapX = 224;
  const branchGapY = 112;
  const padX = 72;
  const padY = 72;
  const state = {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    pan: null,
    selectedNode: null,
    selectedEdge: null,
  };

  const graph = buildGraph();
  layoutGraph(graph);
  renderGraph(graph);
  requestAnimationFrame(() => fitGraph());

  shell.querySelectorAll("[data-live-zoom]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.liveZoom;
      if (action === "in") zoomBy(1.18);
      if (action === "out") zoomBy(0.84);
      if (action === "fit") fitGraph();
      if (action === "reset") {
        state.scale = 1;
        state.offsetX = 0;
        state.offsetY = 0;
        applyTransform();
      }
    });
  });

  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 0.9 : 1.1;
      zoomBy(direction, event.offsetX, event.offsetY);
    },
    { passive: false },
  );

  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (event.target.closest?.(".omega-live-node, .omega-live-edge, .omega-live-edge-label")) {
      return;
    }
    state.pan = {
      x: event.clientX,
      y: event.clientY,
      offsetX: state.offsetX,
      offsetY: state.offsetY,
    };
    viewport.setPointerCapture(event.pointerId);
  });

  viewport.addEventListener("pointermove", (event) => {
    if (!state.pan) return;
    state.offsetX = state.pan.offsetX + (event.clientX - state.pan.x);
    state.offsetY = state.pan.offsetY + (event.clientY - state.pan.y);
    applyTransform();
  });

  viewport.addEventListener("pointerup", (event) => {
    state.pan = null;
    if (viewport.hasPointerCapture(event.pointerId)) {
      viewport.releasePointerCapture(event.pointerId);
    }
  });

  window.addEventListener("resize", () => fitGraph());

  function buildGraph() {
    const nodes = [];
    const edges = [];
    const byAddress = new Map();
    const addressByDepth = new Map();

    function upsertNode(address, next) {
      const key = normalizeAddress(address);
      if (!key) return null;
      let node = byAddress.get(key);
      if (!node) {
        node = {
          id: key,
          address,
          role: next.role || "hop",
          depth: numberOrNull(next.depth) ?? 0,
          amountBase: numberOrNull(next.amountBase) ?? 0,
          status: next.status || "observed",
          meta: next.meta || {},
          x: 0,
          y: 0,
        };
        byAddress.set(key, node);
        nodes.push(node);
      } else {
        node.depth = Math.min(node.depth, numberOrNull(next.depth) ?? node.depth);
        if (next.role === "seed" || (node.role !== "seed" && next.role)) node.role = next.role;
        if (next.amountBase !== undefined && next.amountBase !== null) {
          node.amountBase = Math.max(node.amountBase, Number(next.amountBase));
        }
        node.status = next.status || node.status;
        node.meta = { ...node.meta, ...(next.meta || {}) };
      }
      if (!addressByDepth.has(node.depth)) {
        addressByDepth.set(node.depth, node.address);
      }
      return node;
    }

    const seed = upsertNode(seedAddress, {
      role: "seed",
      depth: 0,
      amountBase: seedAmountBase,
      status: "seed payment",
      meta: {
        txid: result.case?.payment_txid || result.seed_match?.txid,
        ts_ms: result.case?.payment_ts ?? result.case?.payment_ts_ms ?? result.seed_match?.ts_ms,
      },
    });
    if (seed) addressByDepth.set(0, seed.address);

    const hops = Array.isArray(result.hops) ? result.hops : [];
    hops.forEach((hop, index) => {
      const destination = hop.address || hop.destination_address;
      if (!destination) return;
      const depth = numberOrNull(hop.hop) ?? numberOrNull(hop.trace_rank) ?? index + 1;
      const source = hop.source_address || addressByDepth.get(depth - 1) || seedAddress;
      const amountBase = numberOrNull(hop.value_base) ?? numberOrNull(hop.amount_base) ?? 0;
      upsertNode(source, {
        role: normalizeAddress(source) === normalizeAddress(seedAddress) ? "seed" : "hop",
        depth: Math.max(0, depth - 1),
        amountBase,
      });
      const node = upsertNode(destination, {
        role: "hop",
        depth,
        amountBase,
        status: hop.frontier_state || hop.class || "observed",
        meta: hop,
      });
      if (node) addressByDepth.set(depth, node.address);
      addEdge(edges, source, destination, {
        id: hop.event_ref || `${source}->${destination}:${index}`,
        amountBase,
        txid: firstTxid(hop),
        ts_ms: hop.ts_ms,
        state: hop.frontier_state || "expanded",
        label: `H${depth}`,
        meta: hop,
      });
    });

    const parked = Array.isArray(result.parked) ? result.parked : [];
    parked.forEach((branch, index) => {
      const destination = branch.address || branch.destination_address;
      if (!destination) return;
      const fromDepth = numberOrNull(branch.from_hop) ?? 0;
      const source = branch.source_address || addressByDepth.get(fromDepth) || seedAddress;
      const depth = fromDepth + 1;
      const amountBase = numberOrNull(branch.value_base) ?? numberOrNull(branch.amount_base) ?? 0;
      upsertNode(source, {
        role: normalizeAddress(source) === normalizeAddress(seedAddress) ? "seed" : "hop",
        depth: fromDepth,
        amountBase,
      });
      upsertNode(destination, {
        role: "deferred",
        depth,
        amountBase,
        status: branch.deferral_reason || branch.reason || "deferred",
        meta: branch,
      });
      addEdge(edges, source, destination, {
        id: branch.event_ref || branch.branch_id || `${source}->${destination}:parked:${index}`,
        amountBase,
        txid: firstTxid(branch),
        ts_ms: branch.ts_ms,
        state: branch.deferral_reason || branch.reason || "deferred",
        label: "Parked",
        meta: branch,
      });
    });

    if (!edges.length && result.terminal?.kind) {
      const terminalAddress =
        result.terminal.deposit_address ||
        result.terminal.hot_wallet ||
        result.terminal.current_address ||
        "Trace boundary";
      const amountBase =
        numberOrNull(result.terminal.amount_credited_base) ??
        numberOrNull(result.terminal.remaining_amount_base) ??
        seedAmountBase;
      upsertNode(terminalAddress, {
        role: "deferred",
        depth: 1,
        amountBase,
        status: String(result.terminal.kind).replaceAll("_", " "),
        meta: result.terminal,
      });
      addEdge(edges, seedAddress, terminalAddress, {
        id: `terminal:${result.terminal.kind}`,
        amountBase,
        state: "boundary",
        label: String(result.terminal.kind).replaceAll("_", " "),
        meta: result.terminal,
      });
    }

    return { nodes, edges };
  }

  function addEdge(edges, source, target, next) {
    if (!normalizeAddress(source) || !normalizeAddress(target)) return;
    edges.push({
      id: next.id,
      source,
      target,
      amountBase: numberOrNull(next.amountBase) ?? 0,
      txid: next.txid,
      ts_ms: next.ts_ms,
      state: next.state || "expanded",
      label: next.label || "",
      meta: next.meta || {},
    });
  }

  function layoutGraph(nextGraph) {
    const byAddress = new Map(nextGraph.nodes.map((node) => [normalizeAddress(node.address), node]));
    const mainNodes = nextGraph.nodes
      .filter((node) => node.role !== "deferred")
      .sort((a, b) => {
        const aDepth = numberOrNull(a.depth) ?? 0;
        const bDepth = numberOrNull(b.depth) ?? 0;
        return aDepth - bDepth || b.amountBase - a.amountBase || a.address.localeCompare(b.address);
      });

    mainNodes.forEach((node, index) => {
      node.x = padX + index * depthGap;
      node.y = padY + 18;
    });

    const branchesByParent = new Map();
    const unanchored = [];
    nextGraph.nodes
      .filter((node) => node.role === "deferred")
      .forEach((node) => {
        const incoming = nextGraph.edges.find(
          (edge) => normalizeAddress(edge.target) === normalizeAddress(node.address),
        );
        const parent = incoming ? byAddress.get(normalizeAddress(incoming.source)) : null;
        if (!parent) {
          unanchored.push(node);
          return;
        }
        if (!branchesByParent.has(parent.id)) branchesByParent.set(parent.id, []);
        branchesByParent.get(parent.id).push(node);
      });

    branchesByParent.forEach((branches, parentId) => {
      const parent = byAddress.get(parentId);
      if (!parent) return;
      branches.sort((a, b) => b.amountBase - a.amountBase || a.address.localeCompare(b.address));
      const maxRows = Math.max(2, Math.min(4, Math.ceil(Math.sqrt(branches.length))));
      branches.forEach((node, index) => {
        const row = index % maxRows;
        const col = Math.floor(index / maxRows);
        node.x = parent.x + 28 + col * branchGapX;
        node.y = parent.y + nodeHeight + 92 + row * branchGapY;
      });
    });

    unanchored.forEach((node, index) => {
      node.x = padX + (mainNodes.length + Math.floor(index / 3)) * depthGap;
      node.y = padY + 170 + (index % 3) * branchGapY;
    });
  }

  function renderGraph(nextGraph) {
    clear(svg);
    const bounds = graphBounds(nextGraph);
    svg.setAttribute("viewBox", `0 0 ${Math.max(900, bounds.width + padX * 2)} ${Math.max(420, bounds.height + padY * 2)}`);

    const defs = el("defs");
    const marker = el("marker", {
      id: "omega-live-arrowhead",
      markerWidth: "10",
      markerHeight: "10",
      refX: "9",
      refY: "3",
      orient: "auto",
      markerUnits: "strokeWidth",
    });
    marker.appendChild(el("path", { d: "M0,0 L0,6 L9,3 z", class: "omega-live-arrow" }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    const group = el("g", { "data-live-graph-layer": "true" });
    svg.appendChild(group);

    const byAddress = new Map(nextGraph.nodes.map((node) => [normalizeAddress(node.address), node]));
    nextGraph.edges.forEach((edge, index) => {
      const source = byAddress.get(normalizeAddress(edge.source));
      const target = byAddress.get(normalizeAddress(edge.target));
      if (!source || !target) return;
      const pathData = edgePath(source, target, index);
      const edgeLine = el("path", {
        d: pathData,
        class: `omega-live-edge edge-${classSafe(edge.state)}`,
        "stroke-width": edgeWidth(edge.amountBase),
        fill: "none",
        "marker-end": "url(#omega-live-arrowhead)",
        "data-edge-id": edge.id,
      });
      edgeLine.addEventListener("click", (event) => {
        event.stopPropagation();
        selectEdge(edge);
      });
      group.appendChild(edgeLine);

      const mid = edgeMidpoint(source, target, index);
      const label = el("text", {
        x: mid.x,
        y: mid.y - 12,
        "text-anchor": "middle",
        class: "omega-live-edge-label",
        "data-edge-id": edge.id,
      });
      label.textContent = amountLabel(edge.amountBase);
      label.addEventListener("click", (event) => {
        event.stopPropagation();
        selectEdge(edge);
      });
      group.appendChild(label);
    });

    nextGraph.nodes.forEach((node) => {
      const g = el("g", {
        class: `omega-live-node node-${node.role}`,
        transform: `translate(${node.x} ${node.y})`,
        "data-node-id": node.id,
      });
      g.appendChild(el("rect", {
        class: "omega-live-node-box",
        width: nodeWidth,
        height: nodeHeight,
        rx: "9",
      }));
      const depth = el("text", { x: 15, y: 24, class: "omega-live-node-depth" });
      depth.textContent = node.role === "seed" ? "SEED" : `H${numberOrNull(node.depth) ?? 0}`;
      const address = el("text", { x: 15, y: 50, class: "omega-live-node-address" });
      address.textContent = shortAddress(node.address);
      const status = el("text", { x: 15, y: 70, class: "omega-live-node-status" });
      status.textContent = String(node.status || "observed").replaceAll("_", " ");
      const amount = el("text", { x: 15, y: 88, class: "omega-live-node-amount" });
      amount.textContent = amountLabel(node.amountBase);
      g.append(depth, address, status, amount);
      g.addEventListener("click", (event) => {
        event.stopPropagation();
        selectNode(node);
      });
      group.appendChild(g);
    });

    svg.addEventListener("click", () => clearSelection());
    applyTransform();
  }

  function edgePath(source, target, index) {
    const x1 = source.x + nodeWidth;
    const y1 = source.y + nodeHeight / 2;
    const x2 = target.x;
    const y2 = target.y + nodeHeight / 2;
    const spread = Math.min(70, 18 * (index % 4));
    const control = Math.max(86, Math.abs(x2 - x1) * 0.45);
    if (x2 <= x1 + 20) {
      const loopY = Math.max(y1, y2) + 72 + spread;
      return `M${x1} ${y1} C${x1 + 80} ${loopY}, ${x2 - 80} ${loopY}, ${x2} ${y2}`;
    }
    return `M${x1} ${y1} C${x1 + control} ${y1 + spread}, ${x2 - control} ${y2 - spread}, ${x2} ${y2}`;
  }

  function edgeMidpoint(source, target, index) {
    const x1 = source.x + nodeWidth;
    const y1 = source.y + nodeHeight / 2;
    const x2 = target.x;
    const y2 = target.y + nodeHeight / 2;
    return {
      x: (x1 + x2) / 2,
      y: (y1 + y2) / 2 + Math.min(38, 10 * (index % 4)),
    };
  }

  function edgeWidth(amountBase) {
    const values = graph.edges.map((edge) => Math.max(0, numberOrNull(edge.amountBase) ?? 0));
    const maxValue = Math.max(1, ...values);
    const share = Math.max(0, numberOrNull(amountBase) ?? 0) / maxValue;
    return Math.max(3, Math.round((3 + share * 25) * 10) / 10);
  }

  function selectNode(node) {
    state.selectedNode = node;
    state.selectedEdge = null;
    updateSelection();
    details.innerHTML = detailHtml("Address", [
      ["Address", node.address],
      ["Role", node.role === "seed" ? "Reported seed" : node.role],
      ["State", String(node.status || "observed").replaceAll("_", " ")],
      ["Attributed value", amountLabel(node.amountBase)],
      ["Depth", `H${numberOrNull(node.depth) ?? 0}`],
      ["Transaction", firstTxid(node.meta) || "Not recorded"],
    ]);
  }

  function selectEdge(edge) {
    state.selectedNode = null;
    state.selectedEdge = edge;
    updateSelection();
    details.innerHTML = detailHtml("Transfer", [
      ["From", edge.source],
      ["To", edge.target],
      ["Attributed value", amountLabel(edge.amountBase)],
      ["State", String(edge.state || "observed").replaceAll("_", " ")],
      ["Transaction", edge.txid || "Not recorded"],
      ["Observed", formatIst(edge.ts_ms)],
    ]);
  }

  function clearSelection() {
    state.selectedNode = null;
    state.selectedEdge = null;
    updateSelection();
    details.innerHTML = '<p class="muted">No graph item selected.</p>';
  }

  function updateSelection() {
    svg.querySelectorAll(".selected").forEach((item) => item.classList.remove("selected"));
    if (state.selectedNode) {
      svg.querySelectorAll(`[data-node-id="${cssEscape(state.selectedNode.id)}"]`).forEach((item) => {
        item.classList.add("selected");
      });
    }
    if (state.selectedEdge) {
      svg.querySelectorAll(`[data-edge-id="${cssEscape(state.selectedEdge.id)}"]`).forEach((item) => {
        item.classList.add("selected");
      });
    }
  }

  function detailHtml(title, rows) {
    const body = rows
      .map(([label, value]) => (
        `<h3>${escapeHtml(label)}</h3><div class="omega-detail-value mono">${escapeHtml(String(value ?? "Not recorded"))}</div>`
      ))
      .join("");
    return `<strong>${escapeHtml(title)}</strong>${body}`;
  }

  function fitGraph() {
    const bounds = graphBounds(graph);
    const width = viewport.clientWidth || 900;
    const height = viewport.clientHeight || 420;
    const scaleX = width / Math.max(bounds.width + padX * 2, 1);
    const scaleY = height / Math.max(bounds.height + padY * 2, 1);
    const targetScale = Math.min(scaleX, scaleY);
    state.scale = Math.max(0.58, Math.min(1.25, targetScale));
    const centeredX = (width - (bounds.width + padX * 2) * state.scale) / 2;
    const centeredY = (height - (bounds.height + padY * 2) * state.scale) / 2;
    state.offsetX = targetScale < state.scale ? 22 : centeredX;
    state.offsetY = Math.min(28, centeredY);
    applyTransform();
  }

  function zoomBy(multiplier, originX, originY) {
    const width = viewport.clientWidth || 900;
    const height = viewport.clientHeight || 420;
    const ox = originX ?? width / 2;
    const oy = originY ?? height / 2;
    const previousScale = state.scale;
    const nextScale = Math.max(0.22, Math.min(2.8, state.scale * multiplier));
    const worldX = (ox - state.offsetX) / previousScale;
    const worldY = (oy - state.offsetY) / previousScale;
    state.scale = nextScale;
    state.offsetX = ox - worldX * nextScale;
    state.offsetY = oy - worldY * nextScale;
    applyTransform();
  }

  function applyTransform() {
    const group = svg.querySelector("[data-live-graph-layer]");
    if (!group) return;
    group.setAttribute("transform", `translate(${state.offsetX} ${state.offsetY}) scale(${state.scale})`);
  }

  function graphBounds(nextGraph) {
    if (!nextGraph.nodes.length) {
      return { width: 900, height: 420 };
    }
    const maxX = Math.max(...nextGraph.nodes.map((node) => node.x + nodeWidth));
    const minY = Math.min(...nextGraph.nodes.map((node) => node.y));
    const maxY = Math.max(...nextGraph.nodes.map((node) => node.y + nodeHeight));
    return {
      width: maxX + padX,
      height: maxY - minY + padY,
    };
  }

  function amountLabel(amountBase) {
    const value = (numberOrNull(amountBase) ?? 0) / 10 ** assetDecimals;
    return `${value.toLocaleString("en-IN", {
      minimumFractionDigits: value >= 100 ? 2 : 4,
      maximumFractionDigits: 6,
    })} ${assetSymbol}`;
  }

  function formatIst(tsMs) {
    const numeric = numberOrNull(tsMs);
    if (numeric === null) return "Not recorded";
    try {
      return new Intl.DateTimeFormat("en-IN", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "Asia/Kolkata",
      }).format(new Date(numeric));
    } catch (_error) {
      return String(numeric);
    }
  }

  function firstTxid(item) {
    if (!item) return null;
    if (Array.isArray(item.txids) && item.txids.length) return item.txids[0];
    return item.txid || item.transaction_id || null;
  }

  function shortAddress(value) {
    const text = String(value || "");
    if (text.length <= 24) return text;
    return `${text.slice(0, 10)}...${text.slice(-8)}`;
  }

  function normalizeAddress(value) {
    return String(value || "").trim().toLowerCase();
  }

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function classSafe(value) {
    return String(value || "observed").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function el(name, attrs = {}) {
    const node = document.createElementNS(NS, name);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    });
    return node;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
  }
})();
