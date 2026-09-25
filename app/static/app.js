(() => {
  const parseBodyJson = (name, fallback = {}) => {
    try {
      return JSON.parse(document.body?.dataset?.[name] || "") || fallback;
    } catch (_error) {
      return fallback;
    }
  };
  const csrf = document.body?.dataset?.csrfToken || "";
  const navigationRoutes = parseBodyJson("navigationRoutes");
  let workingContext = parseBodyJson("workContext");
  const authenticatedSession = document.body?.classList.contains("authenticated-session");

  if (!authenticatedSession) {
    try {
      window.sessionStorage.removeItem("trinetraWorkingContext");
    } catch (_error) {
      // Public pages must not retain the previous officer's workspace context.
    }
  }

  const navDrawer = document.querySelector("[data-nav-drawer]");
  const navDrawerToggle = document.querySelector("[data-nav-drawer-toggle]");
  const setNavDrawer = (open) => {
    document.body.classList.toggle("nav-drawer-open", open);
    navDrawerToggle?.setAttribute("aria-expanded", String(open));
    const label = navDrawerToggle?.querySelector(".sr-only");
    if (label) label.textContent = open ? "Close navigation" : "Open navigation";
  };
  navDrawerToggle?.addEventListener("click", () => {
    setNavDrawer(!document.body.classList.contains("nav-drawer-open"));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("nav-drawer-open")) {
      setNavDrawer(false);
      navDrawerToggle?.focus();
    }
  });
  navDrawer?.addEventListener("click", (event) => {
    if (event.target.closest("a") && window.innerWidth < 1440) setNavDrawer(false);
  });
  if (authenticatedSession) {
    try {
      const persisted = JSON.parse(window.sessionStorage.getItem("trinetraWorkingContext") || "null");
      if (persisted && persisted.role === workingContext.role) {
        workingContext = { ...workingContext, ...persisted, role: workingContext.role };
      }
    } catch (_error) {
      // Server context remains canonical when sessionStorage is unavailable.
    }
  }

  const persistContext = (context) => {
    workingContext = context;
    try {
      window.sessionStorage.setItem("trinetraWorkingContext", JSON.stringify(context));
    } catch (_error) {
      // Navigation still works through ordinary links.
    }
  };

  const routeFor = (viewId, context) => {
    let route = navigationRoutes[viewId];
    if (!route) return null;
    for (const key of ["case_id", "snapshot_id", "finding_id", "notice_id", "dispatch_id"]) {
      if (route.includes(`{${key}}`)) {
        if (context[key] === null || context[key] === undefined) return null;
        route = route.replace(`{${key}}`, encodeURIComponent(String(context[key])));
      }
    }
    if (viewId === "investigator-canvas" && context.snapshot_id) {
      route += `?snapshot=${encodeURIComponent(String(context.snapshot_id))}`;
    }
    if (context.focus) {
      route += `${route.includes("?") ? "&" : "?"}focus=${encodeURIComponent(context.focus)}`;
    }
    return route;
  };

  window.updateWorkingContext = async (contextPatch = {}) => {
    const response = await fetch("/api/work-context", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
      },
      body: JSON.stringify({ context: { ...workingContext, ...contextPatch } }),
    });
    if (!response.ok) throw new Error(`Context update failed (${response.status})`);
    const context = await response.json();
    persistContext(context);
    return context;
  };

  window.navigate = async (viewId, contextPatch = {}) => {
    const context = await window.updateWorkingContext(contextPatch);
    const destination = routeFor(viewId, context);
    if (!destination) throw new Error(`No route is available for ${viewId}`);
    window.location.assign(destination);
  };

  document.querySelectorAll("a[data-view-id]").forEach((link) => {
    link.addEventListener("click", async (event) => {
      const destination = routeFor(link.dataset.viewId, workingContext);
      if (!destination) return;
      event.preventDefault();
      try {
        await window.navigate(link.dataset.viewId, {});
      } catch (error) {
        console.error("Context navigation failed:", error);
        window.location.assign(link.href);
      }
    });
  });

  const applyWorkspaceState = (state) => {
    if (state.context) persistContext(state.context);
    document.querySelectorAll("[data-nav-count]").forEach((badge) => {
      const value = state.counts?.[badge.dataset.navCount];
      badge.textContent = String(value ?? 0);
      badge.classList.toggle("is-zero", Number(value ?? 0) === 0);
    });
    window.dispatchEvent(new CustomEvent("trinetra:workspace-state", { detail: state }));
  };

  const refreshNavigationState = async () => {
    try {
      const response = await fetch("/api/navigation-state", { credentials: "same-origin" });
      if (!response.ok) return;
      const state = await response.json();
      applyWorkspaceState(state);
    } catch (_error) {
      // Initial server-rendered links remain usable while disconnected.
    }
  };
  if (authenticatedSession) {
    refreshNavigationState();
    if (typeof EventSource !== "undefined") {
      const workspaceSource = new EventSource("/api/workspace/events");
      workspaceSource.addEventListener("workspace", (event) => {
        try { applyWorkspaceState(JSON.parse(event.data)); } catch (_error) { /* keep last verified state */ }
        workspaceSource.close();
      });
      workspaceSource.onerror = () => workspaceSource.close();
      window.addEventListener("pagehide", () => workspaceSource.close(), { once: true });
    }
  }

  document.querySelectorAll("[data-retrace-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const confirmed = window.confirm(
        "Re-run this trace as a new immutable snapshot? The current snapshot remains available and earlier exports are not changed."
      );
      if (!confirmed) event.preventDefault();
    });
  });

  const docketRoot = document.querySelector(".docket-screen");
  if (docketRoot) {
    const rows = [...docketRoot.querySelectorAll("[data-docket-row]")];
    const filters = [...docketRoot.querySelectorAll("[data-docket-filter]")];
    const search = docketRoot.querySelector("#docket-search");
    const empty = docketRoot.querySelector("[data-docket-empty]");
    const count = document.querySelector("[data-docket-count]");
    let activeFilter = "all";
    let sortState = { key: "", direction: 1 };

    const sortDatasetKey = (key) => `sort${key[0].toUpperCase()}${key.slice(1)}`;
    const applyDocketView = () => {
      const query = (search?.value || "").trim().toLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const groups = (row.dataset.filterGroups || "").split(/\s+/);
        const matchesFilter = activeFilter === "all" || groups.includes(activeFilter);
        const matchesSearch = !query || (row.dataset.searchText || "").includes(query);
        const show = matchesFilter && matchesSearch;
        row.hidden = !show;
        if (show) visible += 1;
      });
      if (empty) empty.hidden = visible !== 0;
      if (count) count.textContent = `${visible} of ${rows.length} shown`;
    };

    filters.forEach((button) => {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.docketFilter || "all";
        filters.forEach((item) => {
          const selected = item === button;
          item.classList.toggle("active", selected);
          item.setAttribute("aria-pressed", String(selected));
        });
        applyDocketView();
      });
    });
    search?.addEventListener("input", applyDocketView);

    const acpToggle = docketRoot.querySelector("[data-acp-case-toggle]");
    if (acpToggle) {
      const cards = [...acpToggle.querySelectorAll("[data-acp-case]")];
      const buttons = [...acpToggle.querySelectorAll("[data-acp-case-view]")];
      const visibleCount = acpToggle.querySelector("[data-acp-visible-count]");
      const acpEmpty = acpToggle.querySelector("[data-acp-empty]");
      const applyAcpView = (view) => {
        let visible = 0;
        cards.forEach((card) => {
          const show = view === "all" || card.dataset.escalated === "true";
          card.hidden = !show;
          if (show) visible += 1;
        });
        if (visibleCount) visibleCount.textContent = String(visible);
        if (acpEmpty) acpEmpty.hidden = visible !== 0;
      };
      buttons.forEach((button) => {
        button.addEventListener("click", () => {
          const view = button.dataset.acpCaseView || "all";
          buttons.forEach((item) => {
            const selected = item === button;
            item.classList.toggle("active", selected);
            item.setAttribute("aria-selected", String(selected));
          });
          applyAcpView(view);
        });
      });
      applyAcpView("all");
    }

    docketRoot.querySelectorAll("[data-docket-sort]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.docketSort || "";
        sortState = {
          key,
          direction: sortState.key === key ? sortState.direction * -1 : 1,
        };
        const tbody = rows[0]?.parentElement;
        if (!tbody) return;
        rows.sort((a, b) => {
          const left = a.dataset[sortDatasetKey(key)] || "";
          const right = b.dataset[sortDatasetKey(key)] || "";
          const leftNumber = Number(left);
          const rightNumber = Number(right);
          const comparison = Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
            ? leftNumber - rightNumber
            : left.localeCompare(right);
          return comparison * sortState.direction;
        });
        rows.forEach((row) => tbody.insertBefore(row, empty || null));
        applyDocketView();
      });
    });
  }

  document.addEventListener("click", (event) => {
    const sample = event.target.closest("[data-complaint-sample]");
    if (!sample) return;
    const input = document.querySelector("#ack_no");
    if (!input) return;
    input.value = sample.dataset.complaintSample;
    input.focus();
  });

  document.querySelector("[data-trace-start-form]")?.addEventListener("submit", (event) => {
    const button = event.currentTarget.querySelector("[data-trace-start-button]");
    if (!button) return;
    button.disabled = true;
    button.textContent = "Starting trace...";
  });

  document.querySelectorAll("[data-bottom-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.bottomTab;
      const scope = button.closest(".canvas-bottom-card") || document;
      scope.querySelectorAll("[data-bottom-tab]").forEach((item) => {
        item.setAttribute("aria-selected", String(item === button));
      });
      scope.querySelectorAll("[data-bottom-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.bottomPanel !== tab;
      });
    });
  });

  window.trinetraCopy = async (value) => {
    if (!navigator.clipboard) return false;
    await navigator.clipboard.writeText(value);
    return true;
  };

  const controls = document.querySelector("[data-session-controls]");
  const shield = document.querySelector("[data-session-shield]");
  if (!controls || !shield) return;

  const obscureMs = Number(controls.dataset.idleObscureSeconds || 120) * 1000;
  const warningMs = Number(controls.dataset.idleWarningSeconds || 780) * 1000;
  const timeoutMs = Number(controls.dataset.idleTimeoutSeconds || 900) * 1000;
  const csrfToken = controls.dataset.csrfToken || "";
  const resumeButton = shield.querySelector("[data-session-resume]");
  const message = shield.querySelector("[data-session-shield-message]");
  const countdown = shield.querySelector("[data-session-countdown]");
  let lastActivityAt = Date.now();
  let lastServerTouchAt = Date.now();
  let locked = false;
  let ending = false;

  const dispatchShieldState = (source) => {
    window.dispatchEvent(new CustomEvent("trinetra:session-shield-change", {
      detail: { locked, source },
    }));
  };

  const showShield = (warning, customMessage, source = "idle") => {
    const wasLocked = locked;
    locked = true;
    shield.hidden = false;
    document.body.classList.add("session-obscured");
    if (message) {
      message.textContent = customMessage || (
        warning
          ? "This session is about to end because the workstation has been idle."
          : "Re-enter your active session to reveal case information."
      );
    }
    if (countdown) countdown.hidden = !warning;
    if (!wasLocked) dispatchShieldState(source);
  };

  const hideShield = () => {
    const wasLocked = locked;
    locked = false;
    shield.hidden = true;
    document.body.classList.remove("session-obscured");
    if (countdown) countdown.hidden = true;
    if (wasLocked) dispatchShieldState("manual-resume");
  };

  window.trinetraSessionShield = Object.freeze({
    obscure: (customMessage, source = "external") => {
      showShield(false, customMessage, source);
    },
    isLocked: () => locked,
  });
  window.dispatchEvent(new CustomEvent("trinetra:session-shield-ready"));

  const postSessionAction = async (url) => {
    const body = new URLSearchParams({ csrf_token: csrfToken });
    const response = await fetch(url, {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    if (!response.ok || response.redirected) {
      window.location.assign("/login");
      return false;
    }
    return true;
  };

  const endIdleSession = () => {
    if (ending) return;
    ending = true;
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/auth/logout";
    for (const [name, value] of Object.entries({
      csrf_token: csrfToken,
      reason: "idle_timeout",
    })) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
  };

  const tick = () => {
    const idleMs = Date.now() - lastActivityAt;
    if (idleMs >= timeoutMs) {
      endIdleSession();
      return;
    }
    if (idleMs >= warningMs) {
      showShield(true, undefined, "idle-warning");
      if (countdown) {
        countdown.textContent = `Session ends in ${Math.max(1, Math.ceil((timeoutMs - idleMs) / 1000))} seconds`;
      }
      return;
    }
    if (idleMs >= obscureMs && !locked) showShield(false, undefined, "idle");
  };

  const registerActivity = () => {
    if (locked || document.hidden) return;
    lastActivityAt = Date.now();
    if (lastActivityAt - lastServerTouchAt >= 60000) {
      lastServerTouchAt = lastActivityAt;
      postSessionAction("/auth/session/extend");
    }
  };

  for (const eventName of ["pointerdown", "keydown", "scroll", "touchstart"]) {
    window.addEventListener(eventName, registerActivity, { passive: true });
  }
  document.addEventListener("visibilitychange", tick);
  window.setInterval(tick, 1000);

  resumeButton?.addEventListener("click", async () => {
    resumeButton.disabled = true;
    const resumed = await postSessionAction("/auth/session/extend");
    if (resumed) {
      lastActivityAt = Date.now();
      lastServerTouchAt = lastActivityAt;
      hideShield();
    }
    resumeButton.disabled = false;
  });
})();
