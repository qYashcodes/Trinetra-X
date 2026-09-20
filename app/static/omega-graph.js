(() => {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const shells = [...document.querySelectorAll("[data-omega-graph]")];
  if (!shells.length) return;

  shells.forEach((shell, shellIndex) => initGraph(shell, shellIndex));

  function initGraph(shell, shellIndex) {
    const dataNode = shell.querySelector("[data-omega-graph-json]");
    const viewport = shell.querySelector("[data-omega-viewport]");
    const svg = shell.querySelector("[data-omega-svg]");
    const details = shell.querySelector("[data-omega-details]");
    const workspace = shell.closest("[data-canvas-workspace]") || document;
    const strategySelect = workspace.querySelector("[data-strategy-select]");
    const strategyDelta = workspace.querySelector("[data-strategy-delta]");
    const strategyExplanation = workspace.querySelector("[data-strategy-explanation]");
    const strategyLegend = shell.querySelector("[data-strategy-legend]");
    const strategyFinding = shell.querySelector("[data-strategy-finding] p");
    const strategyChip = shell.querySelector("[data-strategy-chip]");
    const compareButton = shell.querySelector("[data-omega-compare]");
    const ghostLegend = shell.querySelector("[data-ghost-legend]");
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
      analysis: null,
      previousAnalysis: null,
      comparePrevious: false,
    };
    const markerId = `omega-arrow-${shellIndex}`;
    const graph = buildGraph(payload, asset);
    layoutGraph(graph);
    renderGraph();
    requestAnimationFrame(fitGraph);
    if (strategySelect && shell.dataset.strategyEndpoint) {
      loadStrategy(strategySelect.value, null);
      strategySelect.addEventListener("change", async () => {
        const label = strategySelect.selectedOptions[0]?.textContent || strategySelect.value;
        strategySelect.setAttribute("aria-label", `View trace strategy: ${label}`);
        await loadStrategy(strategySelect.value, state.analysis?.strategy || null);
      });
    }

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

    shell.querySelector("[data-omega-relayout]")?.addEventListener("click", () => {
      layoutGraph(graph);
      renderGraph(true);
    });

    compareButton?.addEventListener("click", () => {
      if (!state.previousAnalysis) return;
      state.comparePrevious = !state.comparePrevious;
      compareButton.setAttribute("aria-pressed", String(state.comparePrevious));
      compareButton.textContent = state.comparePrevious ? "Hide comparison" : "Compare previous";
      if (ghostLegend) ghostLegend.hidden = !state.comparePrevious;
      renderGraph();
    });

    shell.querySelector("[data-omega-fullscreen]")?.addEventListener("click", toggleFullscreen);
    shell.querySelectorAll("[data-omega-export]").forEach((button) => {
      button.addEventListener("click", () => exportGraph(button.dataset.omegaExport || "svg"));
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
    document.addEventListener("fullscreenchange", () => {
      const button = shell.querySelector("[data-omega-fullscreen]");
      if (button) {
        const active = document.fullscreenElement === shell || shell.classList.contains("omega-maximized");
        button.textContent = active ? "Exit fullscreen" : "Fullscreen";
        button.setAttribute("aria-label", active ? "Exit graph fullscreen" : "Enter graph fullscreen");
      }
      requestAnimationFrame(applyTransform);
    });
    window.addEventListener("keydown", (event) => {
      if (event.key.toLowerCase() !== "f" || /^(INPUT|SELECT|TEXTAREA)$/.test(event.target?.tagName || "")) return;
      event.preventDefault();
      toggleFullscreen();
    });

    async function loadStrategy(strategy, previousStrategy) {
      const params = new URLSearchParams({ strategy });
      if (previousStrategy) params.set("previous_strategy", previousStrategy);
      strategySelect.disabled = true;
      try {
        const response = await fetch(`${shell.dataset.strategyEndpoint}?${params}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`Strategy request failed (${response.status})`);
        const analysis = await response.json();
        applyStrategy(analysis);
        if (window.updateWorkingContext) await window.updateWorkingContext({ strategy });
      } catch (error) {
        console.error("Strategy view failed:", error);
        if (strategyDelta) strategyDelta.textContent = "Strategy projection is unavailable; the sealed graph remains unchanged.";
      } finally {
        strategySelect.disabled = false;
      }
    }

    function applyStrategy(analysis) {
      const oldPositions = new Map(graph.nodes.map((node) => [node.address, { x: node.x, y: node.y }]));
      if (state.analysis && state.analysis.strategy !== analysis.strategy) {
        state.previousAnalysis = state.analysis;
        state.comparePrevious = false;
      }
      state.analysis = analysis;
      const edgeStyles = new Map((analysis.edges || []).map((edge) => [edge.key, edge]));
      const nodeRanks = new Map((analysis.node_ranks || []).map((node) => [node.address, node]));
      graph.edges.forEach((edge) => {
        const style = edgeStyles.get(edge.stableKey) || {};
        edge.width = Number(style.width_px || edgeWidth(edge.amount));
        edge.primary = Boolean(style.primary);
        edge.deprioritized = Boolean(style.deprioritized);
        edge.strategyRank = style.rank ?? null;
      });
      graph.nodes.forEach((node) => {
        const rank = nodeRanks.get(node.address) || {};
        node.strategyRank = rank.rank ?? 0;
        node.primaryOrder = rank.primary_order ?? null;
        node.primary = (analysis.primary_path || []).includes(node.address);
      });
      layoutGraph(graph);
      renderGraph(true, oldPositions);
      if (compareButton) {
        compareButton.disabled = !state.previousAnalysis;
        compareButton.title = state.previousAnalysis
          ? `Compare with ${state.previousAnalysis.strategy.replaceAll("_", " ")}`
          : "Select another strategy to compare";
        compareButton.textContent = "Compare previous";
      }
      if (ghostLegend) ghostLegend.hidden = true;
      if (strategyDelta) strategyDelta.textContent = analysis.delta?.summary || analysis.criterion;
      if (strategyLegend) strategyLegend.textContent = `${analysis.strategy.replaceAll("_", " ")}: ${analysis.edge_width_mapping}`;
      if (strategyChip) strategyChip.textContent = analysis.strategy.replaceAll("_", " ");
      if (strategyExplanation) {
        const explanation = analysis.explanation || {};
        strategyExplanation.textContent = `${explanation.criterion || analysis.criterion} Selected ${explanation.selected_hop_count || 0} hop(s), with ${formatBaseUnits(explanation.selected_value_base, asset.decimals)} ${asset.symbol || "asset"} surviving on the primary path. ${explanation.terminal_statement || ""}`;
      }
      if (strategyFinding) {
        const finding = analysis.finding_projection || {};
        strategyFinding.textContent = finding.status === "recorded_terminal_reached"
          ? `The selected priority path reaches ${shortAddress(finding.deposit_address)}. The recorded custody finding remains tied to this sealed snapshot.`
          : `The selected priority path ends at ${shortAddress(finding.deposit_address)}. This analytical projection does not replace the recorded custody finding; a reviewed retrace is required.`;
      }
    }

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
            stableKey: `${currentNode.address}>${childNode.address}:${item.output_index ?? index}`,
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
        group.sort((left, right) => {
          const leftPrimary = left.primaryOrder === null || left.primaryOrder === undefined ? 9999 : left.primaryOrder;
          const rightPrimary = right.primaryOrder === null || right.primaryOrder === undefined ? 9999 : right.primaryOrder;
          return leftPrimary - rightPrimary || (left.strategyRank || 9999) - (right.strategyRank || 9999) || left.address.localeCompare(right.address);
        });
        const totalHeight = group.length * 108 + Math.max(0, group.length - 1) * rowGap;
        const startY = padY + Math.max(0, 250 - totalHeight / 2);
        group.forEach((node, index) => {
          node.x = padX + (depth - 1) * depthGap;
          node.y = startY + index * (node.height + rowGap);
        });
      });
    }

    function renderGraph(animate = false, previousPositions = null) {
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

      if (state.comparePrevious && state.previousAnalysis) {
        const previousKeys = new Set(state.previousAnalysis.primary_edge_keys || []);
        graph.edges.filter((edge) => previousKeys.has(edge.stableKey)).forEach((edge) => {
          const source = graph.nodes.find((node) => node.id === edge.source);
          const target = graph.nodes.find((node) => node.id === edge.target);
          if (!source || !target) return;
          const geometry = edgeGeometry(source, target);
          group.appendChild(createSvg("path", {
            d: geometry.path,
            fill: "none",
            stroke: "currentColor",
            "stroke-width": String(Math.max(2, edge.width || 2)),
            class: "omega-ghost-edge",
          }));
        });
      }

      graph.edges.forEach((edge) => {
        const source = graph.nodes.find((node) => node.id === edge.source);
        const target = graph.nodes.find((node) => node.id === edge.target);
        if (!source || !target) return;
        const geometry = edgeGeometry(source, target);
        const path = createSvg("path", {
          d: geometry.path,
          fill: "none",
          stroke: "currentColor",
          "stroke-width": String(edge.width || edgeWidth(edge.amount)),
          "marker-end": `url(#${markerId})`,
          class: `omega-flow-edge edge-${edge.state}${edge.primary ? " is-primary" : ""}${edge.deprioritized ? " is-deprioritized" : ""}`,
          "data-edge-id": edge.id,
          "data-edge-key": edge.stableKey,
        });
        path.addEventListener("click", (event) => {
          event.stopPropagation();
          selectEdge(edge.id);
        });
        group.appendChild(path);

        const label = createSvg("text", {
          x: String((geometry.sx + geometry.ex) / 2),
          y: String((geometry.sy + geometry.ey) / 2 - 8),
          "text-anchor": "middle",
          class: `omega-edge-label edge-${edge.state}${edge.deprioritized ? " is-deprioritized" : ""}`,
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
          class: `omega-graph-node node-${node.role}${node.primary ? " is-primary" : ""}`,
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
        role.textContent = `${node.role === "seed" ? "SEED" : node.role.toUpperCase()}${node.strategyRank ? ` · R${node.strategyRank}` : ""}`;
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
        if (animate && previousPositions?.has(node.address) && g.animate) {
          const previous = previousPositions.get(node.address);
          g.animate(
            [
              { transform: `translate(${previous.x}px, ${previous.y}px)` },
              { transform: `translate(${node.x}px, ${node.y}px)` },
            ],
            { duration: 400, easing: "ease-in-out" },
          );
        }
      });
      applyTransform();
      updateSelection();
    }

    function edgeGeometry(source, target) {
      const sx = source.x + source.width;
      const sy = source.y + source.height / 2;
      const ex = target.x;
      const ey = target.y + target.height / 2;
      const curve = Math.max(100, (ex - sx) * 0.45);
      return { sx, sy, ex, ey, path: `M ${sx} ${sy} C ${sx + curve} ${sy}, ${ex - curve} ${ey}, ${ex} ${ey}` };
    }

    async function toggleFullscreen() {
      if (document.fullscreenElement === shell) {
        await document.exitFullscreen();
        return;
      }
      if (shell.classList.contains("omega-maximized")) {
        shell.classList.remove("omega-maximized");
        document.body.classList.remove("omega-fullscreen-fallback");
        return;
      }
      try {
        if (!shell.requestFullscreen) throw new Error("Fullscreen API unavailable");
        await shell.requestFullscreen();
      } catch (_error) {
        shell.classList.add("omega-maximized");
        document.body.classList.add("omega-fullscreen-fallback");
      }
      requestAnimationFrame(applyTransform);
    }

    async function exportGraph(format) {
      const clone = svg.cloneNode(true);
      const scope = shell.querySelector("[data-omega-export-scope]")?.value || "whole_graph";
      const maxX = graph.nodes.length ? Math.max(...graph.nodes.map((node) => node.x + node.width)) : 1200;
      const maxY = graph.nodes.length ? Math.max(...graph.nodes.map((node) => node.y + node.height)) : 700;
      const width = scope === "visible_area" ? Math.max(1, viewport.clientWidth) : Math.max(1, maxX + 40);
      const height = scope === "visible_area" ? Math.max(1, viewport.clientHeight) : Math.max(1, maxY + 40);
      const captionHeight = 54;
      if (scope === "whole_graph") {
        clone.querySelector("[data-omega-group]")?.setAttribute("transform", "translate(20,20) scale(1)");
      }
      clone.setAttribute("xmlns", NS);
      clone.setAttribute("viewBox", `0 0 ${width} ${height + captionHeight}`);
      clone.setAttribute("width", String(width));
      clone.setAttribute("height", String(height + captionHeight));
      const background = createSvg("rect", { x: "0", y: "0", width: String(width), height: String(height + captionHeight), fill: "#0e1825" });
      clone.insertBefore(background, clone.firstChild);
      const captionBand = createSvg("rect", { x: "0", y: String(height), width: String(width), height: String(captionHeight), fill: "#ffffff" });
      const caption = createSvg("text", { x: "20", y: String(height + 33), fill: "#172033", "font-size": "16", "font-family": "Noto Sans, sans-serif" });
      const strategy = state.analysis?.strategy || strategySelect?.value || "strategy";
      const stamp = formatIstForCaption(new Date());
      caption.textContent = `Case ${shell.dataset.caseId || "-"} · Trace ${shell.dataset.traceId || "-"} · Strategy ${strategy} · Generated ${stamp} · Trinetra`;
      clone.appendChild(captionBand);
      clone.appendChild(caption);
      const source = new XMLSerializer().serializeToString(clone);
      const svgBlob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
      const baseName = `trinetra_graph_${shell.dataset.caseId || "case"}_${shell.dataset.traceId || "trace"}_${strategy}_${fileStamp(new Date())}`;
      let blob = svgBlob;
      let extension = "svg";
      if (format === "png") {
        blob = await svgToPng(svgBlob, width, height + captionHeight);
        extension = "png";
      }
      const detail = { blob, format: extension, filename: `${baseName}.${extension}`, strategy, scope };
      shell.dispatchEvent(new CustomEvent("trinetra:graph-export", { detail, bubbles: true }));
      downloadBlob(blob, detail.filename);
      window.trinetraLastGraphExport = detail;
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

  function formatBaseUnits(amountBase, decimals) {
    const base = Number(amountBase || 0);
    const divisor = 10 ** decimalsFor(decimals, 6);
    return formatAmount(base / divisor, decimals);
  }

  function formatIstForCaption(date) {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date).replace(",", "") + " IST";
  }

  function fileStamp(date) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(date).reduce((acc, part) => ({ ...acc, [part.type]: part.value }), {});
    return `${parts.year}${parts.month}${parts.day}-${parts.hour}${parts.minute}`;
  }

  async function svgToPng(svgBlob, width, height) {
    const url = URL.createObjectURL(svgBlob);
    try {
      const image = new Image();
      image.decoding = "async";
      const loaded = new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = reject;
      });
      image.src = url;
      await loaded;
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(width * 2));
      canvas.height = Math.max(1, Math.round(height * 2));
      const context = canvas.getContext("2d");
      context.scale(2, 2);
      context.drawImage(image, 0, 0, width, height);
      return await new Promise((resolve, reject) => {
        canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("PNG export failed")), "image/png");
      });
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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
