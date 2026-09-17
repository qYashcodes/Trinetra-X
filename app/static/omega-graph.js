(() => {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const shells = [...document.querySelectorAll("[data-omega-graph]")];
  document.querySelectorAll("[data-strategy-select]").forEach((select) => {
    select.addEventListener("change", () => {
      const label = select.selectedOptions[0]?.textContent || select.value;
      select.setAttribute("aria-label", `View trace strategy: ${label}`);
    });
  });
  if (!shells.length) return;

  shells.forEach((shell, shellIndex) => initGraph(shell, shellIndex));

  function initGraph(shell, shellIndex) {
    const dataNode = shell.querySelector("[data-omega-graph-json]");
    const viewport = shell.querySelector("[data-omega-viewport]");
    const svg = shell.querySelector("[data-omega-svg]");
    const details = shell.querySelector("[data-omega-details]");
    if (!dataNode || !viewport || !svg || !details) return;

    let payload = {};
    try {
      payload = JSON.parse(dataNode.textContent || "{}");
    } catch (_error) {
      payload = {};
    }

    const asset = payload.asset || {};
    const state = {
      scale: 1,
      offsetX: 0,
      offsetY: 0,
      pan: null,
      selectedNode: null,
      selectedEdge: null,
      showValues: true,
      showParked: true,
    };
    const markerId = `omega-arrow-${shellIndex}`;
    const graph = buildGraph(payload, asset);
    layoutGraph(graph);
    renderGraph();
    requestAnimationFrame(fitGraph);

    shell.querySelectorAll("[data-omega-zoom]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.omegaZoom;
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

    shell.querySelectorAll("[data-omega-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.omegaToggle;
        if (key === "values") state.showValues = !state.showValues;
        if (key === "parked") state.showParked = !state.showParked;
        button.setAttribute("aria-checked", String(key === "values" ? state.showValues : state.showParked));
        shell.classList.toggle("omega-hide-values", !state.showValues);
        shell.classList.toggle("omega-hide-parked", !state.showParked);
      });
    });

    viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomBy(event.deltaY > 0 ? 0.9 : 1.1, event.offsetX, event.offsetY);
    }, { passive: false });

    viewport.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      if (event.target.closest?.(".omega-graph-node, .omega-flow-edge, .omega-edge-label")) return;
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
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    });

    window.addEventListener("resize", fitGraph);

    function buildGraph(data, assetData) {
      const nodes = [];
      const edges = [];
      let nodeCounter = 0;
      let edgeCounter = 0;
      const currentUnit = assetData.symbol || "USDT";
      const currentDecimals = Number(assetData.decimals ?? 6);

      function addNode(source, depth, parentId = null) {
        const metadata = source.metadata || {};
        const amount = numberOrZero(source.amount_display ?? (Number(source.amount || 0) / (10 ** decimalsFor(source.decimals, currentDecimals))));
        const node = {
          id: `node_${nodeCounter++}`,
          address: source.address || "Trace boundary",
          depth,
          role: source.role || (depth === 1 ? "seed" : "hop"),
          title: metadata.title || source.role || `Depth ${depth}`,
          amount,
          unit: source.token || currentUnit,
          decimals: decimalsFor(source.decimals, currentDecimals),
          balanceAvailable: Boolean(source.balance?.available),
          balance: numberOrZero(source.balance?.display),
          balanceSymbol: source.balance?.symbol || source.token || currentUnit,
          balanceSource: source.balance?.source || "",
          balanceRetrievedAt: source.balance?.retrieval_ts_ms,
          balanceReason: source.balance?.reason || "",
          explainTarget: source.explain_target || metadata.explain_target,
          metadata,
          x: 0,
          y: 0,
          width: 248,
          height: 108,
          parentId,
        };
        nodes.push(node);
        return node;
      }

      function walk(treeNode, parentNode, depth) {
        if (!treeNode) return;
        const currentNode = parentNode || addNode(treeNode, depth, null);
        (treeNode.children || []).forEach((item, index) => {
          const childSource = item.child || {
            address: item.recipient,
            role: "hop",
            token: item.token,
            decimals: item.decimals,
            amount: item.amount,
            amount_display: item.amount_display,
            metadata: item.metadata || {},
            explain_target: item.explain_target,
          };
          const childNode = addNode(
            {
              ...childSource,
              address: childSource.address || item.recipient,
              amount: item.amount,
              amount_display: item.amount_display,
              token: item.token || childSource.token,
              decimals: item.decimals ?? childSource.decimals,
              explain_target: childSource.explain_target || item.explain_target,
              metadata: {
                ...(childSource.metadata || {}),
                ...(item.metadata || {}),
              },
            },
            depth + 1,
            currentNode.id,
          );
          const amount = numberOrZero(item.amount_display ?? (Number(item.amount || 0) / (10 ** decimalsFor(item.decimals, currentDecimals))));
          const edgeMeta = item.metadata || {};
          edges.push({
            id: `edge_${edgeCounter++}`,
            source: currentNode.id,
            target: childNode.id,
            amount,
            unit: item.token || currentUnit,
            decimals: decimalsFor(item.decimals, currentDecimals),
            txid: item.sent_txid,
            outputIndex: item.output_index ?? index,
            explainTarget: item.explain_target || edgeMeta.explain_target || childNode.explainTarget,
            branchReasons: item.branch_reasons || [],
            state: String(edgeMeta.frontier_state || edgeMeta.kind || childNode.role || "expanded").replaceAll("_", "-"),
            metadata: edgeMeta,
          });
          if (item.child) walk(item.child, childNode, depth + 1);
        });
      }

      walk(data.tree, null, 1);
      return { nodes, edges };
    }

    function layoutGraph(graphData) {
      const groups = new Map();
      graphData.nodes.forEach((node) => {
        if (!groups.has(node.depth)) groups.set(node.depth, []);
        groups.get(node.depth).push(node);
      });
      const depthGap = 310;
      const rowGap = 38;
      const padX = 70;
      const padY = 72;
      [...groups.entries()].sort((a, b) => a[0] - b[0]).forEach(([depth, group]) => {
        const totalHeight = group.length * 108 + Math.max(0, group.length - 1) * rowGap;
        const startY = padY + Math.max(0, 250 - totalHeight / 2);
        group.forEach((node, index) => {
          node.x = padX + (depth - 1) * depthGap;
          node.y = startY + index * (node.height + rowGap);
        });
      });
    }

    function renderGraph() {
      svg.innerHTML = "";
      const defs = createSvg("defs");
      const marker = createSvg("marker", {
        id: markerId,
        markerWidth: "10",
        markerHeight: "10",
        refX: "9",
        refY: "3",
        orient: "auto",
        markerUnits: "strokeWidth",
      });
      marker.appendChild(createSvg("path", { d: "M0,0 L0,6 L9,3 z", class: "omega-arrow" }));
      defs.appendChild(marker);
      svg.appendChild(defs);
      if (!graph.nodes.length) return;

      const maxX = Math.max(...graph.nodes.map((node) => node.x + node.width), 600);
      const maxY = Math.max(...graph.nodes.map((node) => node.y + node.height), 400);
      svg.setAttribute("width", String(maxX + 200));
      svg.setAttribute("height", String(maxY + 150));

      const group = createSvg("g", { "data-omega-group": "true" });
      svg.appendChild(group);

      graph.edges.forEach((edge) => {
        const source = graph.nodes.find((node) => node.id === edge.source);
        const target = graph.nodes.find((node) => node.id === edge.target);
        if (!source || !target) return;
        const sx = source.x + source.width;
        const sy = source.y + source.height / 2;
        const ex = target.x;
        const ey = target.y + target.height / 2;
        const curve = Math.max(100, (ex - sx) * 0.45);
        const path = createSvg("path", {
          d: `M ${sx} ${sy} C ${sx + curve} ${sy}, ${ex - curve} ${ey}, ${ex} ${ey}`,
          fill: "none",
          stroke: "currentColor",
          "stroke-width": String(edgeWidth(edge.amount)),
          "marker-end": `url(#${markerId})`,
          class: `omega-flow-edge edge-${edge.state}`,
          "data-edge-id": edge.id,
        });
        path.addEventListener("click", (event) => {
          event.stopPropagation();
          selectEdge(edge.id);
        });
        group.appendChild(path);

        const label = createSvg("text", {
          x: String((sx + ex) / 2),
          y: String((sy + ey) / 2 - 8),
          "text-anchor": "middle",
          class: `omega-edge-label edge-${edge.state}`,
          "data-edge-id": edge.id,
        });
        label.textContent = `${formatAmount(edge.amount, edge.decimals)} ${edge.unit}`;
        label.addEventListener("click", (event) => {
          event.stopPropagation();
          selectEdge(edge.id);
        });
        group.appendChild(label);
      });

      graph.nodes.forEach((node) => {
        const g = createSvg("g", {
          class: `omega-graph-node node-${node.role}`,
          transform: `translate(${node.x},${node.y})`,
          tabindex: "0",
          role: "button",
          "data-node-id": node.id,
        });
        g.appendChild(createSvg("rect", {
          width: String(node.width),
          height: String(node.height),
          rx: "8",
          ry: "8",
          class: "omega-node-box",
        }));
        const role = createSvg("text", { x: "14", y: "21", class: "omega-node-role" });
        role.textContent = node.role === "seed" ? "SEED" : node.role.toUpperCase();
        g.appendChild(role);
        const addr = createSvg("text", { x: "14", y: "46", class: "omega-node-address" });
        addr.textContent = shortAddress(node.address);
        g.appendChild(addr);
        const amount = createSvg("text", { x: "14", y: "73", class: "omega-node-amount" });
        amount.textContent = `${formatAmount(node.amount, node.decimals)} ${node.unit}`;
        g.appendChild(amount);
        const depth = createSvg("text", { x: "14", y: "95", class: "omega-node-depth" });
        depth.textContent = node.depth === 1 ? "reported anchor" : `depth ${node.depth - 1}`;
        g.appendChild(depth);
        g.addEventListener("click", async (event) => {
          event.stopPropagation();
          selectNode(node.id);
          await copyAddress(node.address);
        });
        g.addEventListener("keydown", async (event) => {
          if (!["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          selectNode(node.id);
          await copyAddress(node.address);
        });
        group.appendChild(g);
      });
      applyTransform();
      updateSelection();
    }

    function selectNode(id) {
      state.selectedNode = graph.nodes.find((node) => node.id === id) || null;
      state.selectedEdge = null;
      updateSelection();
      renderNodeDetails(state.selectedNode);
    }

    function selectEdge(id) {
      state.selectedEdge = graph.edges.find((edge) => edge.id === id) || null;
      state.selectedNode = null;
      updateSelection();
      renderEdgeDetails(state.selectedEdge);
    }

    function updateSelection() {
      shell.querySelectorAll(".omega-graph-node").forEach((node) => {
        node.classList.toggle("selected", Boolean(state.selectedNode && node.dataset.nodeId === state.selectedNode.id));
      });
      shell.querySelectorAll(".omega-flow-edge, .omega-edge-label").forEach((edge) => {
        edge.classList.toggle("selected", Boolean(state.selectedEdge && edge.dataset.edgeId === state.selectedEdge.id));
      });
    }

    function renderNodeDetails(node) {
      if (!node) return;
      const balance = node.balanceAvailable
        ? `${formatAmount(node.balance, node.decimals)} ${escapeHtml(node.balanceSymbol)}`
        : `Unavailable${node.balanceReason ? ` (${escapeHtml(node.balanceReason.replaceAll("_", " "))})` : ""}`;
      const balanceMeta = node.balanceAvailable
        ? `
        <h3>Balance source</h3>
        <div class="omega-detail-value">${escapeHtml(node.balanceSource || "Provider")}${node.balanceRetrievedAt ? ` - ${escapeHtml(formatTimestamp(node.balanceRetrievedAt))}` : ""}</div>`
        : "";
      const confirmed = metadataAmount(node.metadata, "observed_amount_display", "observed_amount_base", node.amount, node.decimals);
      const attributed = metadataAmount(node.metadata, "attributed_amount_display", "attributed_amount_base", node.amount, node.decimals);
      details.innerHTML = `
        <h3>Address</h3>
        <div class="omega-detail-value copyable-address" data-copy-address title="Click to copy">${escapeHtml(node.address)}</div>
        <h3>Role</h3>
        <div class="omega-detail-value">${escapeHtml(node.role.replaceAll("_", " "))}</div>
        <h3>Wallet balance</h3>
        <div class="omega-detail-value">${balance}</div>
        ${balanceMeta}
        <h3>Confirmed transfer amount</h3>
        <div class="omega-detail-value">${escapeHtml(confirmed)} ${escapeHtml(node.unit)}</div>
        <h3>Attributed share</h3>
        <div class="omega-detail-value">${escapeHtml(attributed)} ${escapeHtml(node.unit)}</div>
        <div class="omega-detail-actions">${node.explainTarget ? `<button type="button" data-explain-target="${escapeHtml(node.explainTarget)}">Explain this item</button>` : ""}</div>
      `;
      details.querySelector("[data-copy-address]")?.addEventListener("click", () => copyAddress(node.address));
    }

    function renderEdgeDetails(edge) {
      if (!edge) return;
      const reasons = edge.branchReasons.length
        ? `<ul class="omega-reasons">${edge.branchReasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
        : `<p class="omega-muted">No branch reason was recorded.</p>`;
      const confirmed = metadataAmount(edge.metadata, "observed_amount_display", "observed_amount_base", edge.amount, edge.decimals);
      const attributed = metadataAmount(edge.metadata, "attributed_amount_display", "attributed_amount_base", edge.amount, edge.decimals);
      details.innerHTML = `
        <h3>Confirmed transfer amount</h3>
        <div class="omega-detail-value">${escapeHtml(confirmed)} ${escapeHtml(edge.unit)}</div>
        <h3>Attributed share</h3>
        <div class="omega-detail-value">${escapeHtml(attributed)} ${escapeHtml(edge.unit)}</div>
        <h3>Transaction hash</h3>
        <div class="omega-detail-value mono">${escapeHtml(edge.txid || "Not recorded")}</div>
        <h3>Provider output index</h3>
        <div class="omega-detail-value mono">${escapeHtml(edge.outputIndex ?? "Not recorded")}</div>
        <h3>Branch reasons</h3>
        ${reasons}
        <div class="omega-detail-actions">${edge.explainTarget ? `<button type="button" data-explain-target="${escapeHtml(edge.explainTarget)}">Explain this transfer</button>` : ""}</div>
      `;
    }

    function zoomBy(factor, originX = viewport.clientWidth / 2, originY = viewport.clientHeight / 2) {
      const nextScale = Math.max(0.15, Math.min(5, state.scale * factor));
      const graphX = (originX - state.offsetX) / state.scale;
      const graphY = (originY - state.offsetY) / state.scale;
      state.offsetX = originX - graphX * nextScale;
      state.offsetY = originY - graphY * nextScale;
      state.scale = nextScale;
      applyTransform();
    }

    function fitGraph() {
      if (!graph.nodes.length) return;
      const maxX = Math.max(...graph.nodes.map((node) => node.x + node.width));
      const maxY = Math.max(...graph.nodes.map((node) => node.y + node.height));
      const scaleX = (viewport.clientWidth - 80) / Math.max(1, maxX + 80);
      const scaleY = (viewport.clientHeight - 80) / Math.max(1, maxY + 80);
      state.scale = Math.max(0.15, Math.min(1.35, Math.min(scaleX, scaleY)));
      state.offsetX = Math.max(30, (viewport.clientWidth - maxX * state.scale) / 2);
      state.offsetY = Math.max(30, (viewport.clientHeight - maxY * state.scale) / 2);
      applyTransform();
    }

    function applyTransform() {
      const group = svg.querySelector("[data-omega-group]");
      if (group) group.setAttribute("transform", `translate(${state.offsetX},${state.offsetY}) scale(${state.scale})`);
    }
  }

  function createSvg(tag, attrs = {}) {
    const element = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function edgeWidth(amount) {
    if (amount <= 0) return 2;
    const scaled = Math.log10(amount + 1) * 1.8;
    return Math.max(2, Math.min(14, scaled));
  }

  function formatAmount(amount, decimals) {
    const value = Number(amount || 0);
    const maximumFractionDigits = Number(decimals) === 0 ? 0 : 6;
    return value.toLocaleString("en-US", {
      minimumFractionDigits: value >= 1 ? 2 : 0,
      maximumFractionDigits,
    });
  }

  function shortAddress(address) {
    const value = String(address || "");
    if (value.length <= 18) return value;
    return `${value.slice(0, 7)}...${value.slice(-6)}`;
  }

  function formatTimestamp(tsMs) {
    const numeric = Number(tsMs);
    if (!Number.isFinite(numeric)) return "time not recorded";
    try {
      return new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(numeric));
    } catch (_error) {
      return "time not recorded";
    }
  }

    function decimalsFor(value, fallback) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function metadataAmount(metadata, displayKey, baseKey, fallbackAmount, decimals) {
      const displayValue = metadata?.[displayKey];
      if (displayValue !== undefined && displayValue !== null && displayValue !== "") {
        return formatAmount(displayValue, decimals);
      }
      const baseValue = metadata?.[baseKey];
      if (baseValue !== undefined && baseValue !== null && baseValue !== "") {
        return formatAmount(Number(baseValue) / (10 ** decimalsFor(decimals, 6)), decimals);
      }
      return formatAmount(fallbackAmount, decimals);
    }

    function numberOrZero(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : 0;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function copyAddress(address) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(address);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = address;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      showCopyToast(`Copied ${shortAddress(address)}`);
    } catch (error) {
      console.error("Copy failed:", error);
      showCopyToast("Copy failed - select the address from the details panel.");
    }
  }

  function showCopyToast(message) {
    let toast = document.getElementById("copyToast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "copyToast";
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(window.__copyToastTimer);
    window.__copyToastTimer = window.setTimeout(() => toast.classList.remove("show"), 1800);
  }
})();
