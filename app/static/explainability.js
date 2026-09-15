(() => {
  "use strict";

  const dataNode = document.querySelector("[data-trace-explainability]");
  const dialog = document.querySelector("[data-trace-explain-dialog]");
  if (!dataNode || !dialog) return;

  const data = JSON.parse(dataNode.textContent);
  const items = new Map(data.items.map((item) => [item.id, item]));
  const content = dialog.querySelector("[data-explain-content]");
  const title = dialog.querySelector("[data-explain-title]");
  const meta = dialog.querySelector("[data-explain-meta]");
  const registerLabel = dialog.querySelector("[data-explain-register-label]");
  let selectedId = "methodology";
  let register = data.default_register || "investigator";
  let returnFocus = null;

  const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const formatIst = (timestampMs) => {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }).formatToParts(new Date(Number(timestampMs))).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} IST`;
  };

  const displayValue = (key, value) => {
    if (value === null || value === undefined) return "not recorded";
    if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value, null, 2);
    if (key.endsWith("_ts_ms")) return `${value}\n${formatIst(value)}`;
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  };

  const factRows = (facts, prefix = "") => {
    if (!facts || typeof facts !== "object") return [];
    return Object.entries(facts).map(([key, value]) => {
      const label = prefix ? `${prefix}.${key}` : key;
      return `<div><dt>${escapeHtml(label.replaceAll("_", " "))}</dt><dd>${escapeHtml(displayValue(key, value))}</dd></div>`;
    });
  };

  function render() {
    const item = items.get(selectedId) || items.get("methodology") || data.items[0];
    title.textContent = item.title;
    meta.textContent = [item.address, item.display_amount].filter(Boolean).join(" | ");
    registerLabel.textContent = data.registers[register];
    dialog.querySelectorAll("[data-explain-subject]").forEach((button) => {
      button.setAttribute("aria-current", String(button.dataset.explainSubject === item.id));
    });
    dialog.querySelectorAll("[data-explain-register]").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.explainRegister === register));
    });
    content.innerHTML = item.registers[register].map((section) => {
      const rows = factRows(section.facts);
      return `<section><h3>${escapeHtml(section.title)}</h3><p>${escapeHtml(section.body)}</p>${rows.length ? `<dl class="trace-explain-facts">${rows.join("")}</dl>` : ""}</section>`;
    }).join("");
    content.scrollTop = 0;
  }

  function openExplanation(id, trigger) {
    if (items.has(id)) selectedId = id;
    returnFocus = trigger || document.activeElement;
    render();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    dialog.querySelector("[data-explain-close]").focus();
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-explain-target]");
    if (trigger) openExplanation(trigger.dataset.explainTarget || "methodology", trigger);
  });

  document.addEventListener("keydown", (event) => {
    const trigger = event.target.closest("[data-explain-target]");
    if (!trigger || !["Enter", " "].includes(event.key) || ["BUTTON", "A"].includes(trigger.tagName)) return;
    event.preventDefault();
    openExplanation(trigger.dataset.explainTarget || "methodology", trigger);
  });

  dialog.querySelectorAll("[data-explain-subject]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.explainSubject;
      render();
    });
  });

  dialog.querySelectorAll("[data-explain-register]").forEach((button) => {
    button.addEventListener("click", () => {
      register = button.dataset.explainRegister;
      render();
    });
  });

  const close = () => {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };
  dialog.querySelector("[data-explain-close]").addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => returnFocus?.focus());
  render();
})();
