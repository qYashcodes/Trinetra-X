(() => {
  "use strict";
  const root = document.querySelector("[data-notice-workflow]");
  if (!root) return;

  const parameters = root.querySelector("[data-parameter-form]");
  const autosaveState = root.querySelector("[data-autosave-state]");
  let parametersDirty = false;
  parameters?.addEventListener("input", () => { parametersDirty = true; if (autosaveState) autosaveState.textContent = "Unsaved changes"; });
  const autosave = window.setInterval(async () => {
    if (!parametersDirty || !parameters) return;
    try {
      const response = await fetch(parameters.action, { method: "POST", body: new FormData(parameters), credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
      parametersDirty = false;
      if (autosaveState) autosaveState.textContent = "Autosaved";
    } catch (_error) {
      if (autosaveState) autosaveState.textContent = "Autosave failed — use Save parameters";
    }
  }, 20000);
  window.addEventListener("pagehide", () => window.clearInterval(autosave), { once: true });

  const preview = root.querySelector("[data-preview-scroll]");
  const previewComplete = root.querySelector("[data-preview-complete]");
  const attest = root.querySelector("[data-attest]");
  const previewHelp = root.querySelector("[data-preview-help]");
  const checkPreview = () => {
    if (!preview || !previewComplete || !attest) return;
    const scrollRoot = preview.contentDocument?.scrollingElement || preview;
    const reached = scrollRoot.scrollTop + scrollRoot.clientHeight >= scrollRoot.scrollHeight - 8;
    if (!reached) return;
    previewComplete.value = "true";
    attest.disabled = false;
    if (previewHelp) previewHelp.textContent = "Full preview reached; attestation is unlocked.";
  };
  preview?.addEventListener("load", () => {
    preview.contentDocument?.addEventListener("scroll", checkPreview, { passive: true });
    checkPreview();
  });

  const modal = root.querySelector("[data-verification-modal]");
  const open = root.querySelector("[data-open-verification]");
  const close = root.querySelector("[data-close-verification]");
  let trigger = null;
  const focusables = () => [...modal.querySelectorAll("button:not([disabled]), input:not([disabled])")];
  const closeModal = () => {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    trigger?.focus();
  };
  open?.addEventListener("click", () => {
    trigger = document.activeElement;
    modal.hidden = false;
    focusables()[0]?.focus();
  });
  close?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (event) => { if (event.target === modal) closeModal(); });
  document.addEventListener("keydown", (event) => {
    if (!modal || modal.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeModal(); return; }
    if (event.key !== "Tab") return;
    const items = focusables();
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    if (event.shiftKey && index <= 0) { event.preventDefault(); items.at(-1).focus(); }
    else if (!event.shiftKey && index === items.length - 1) { event.preventDefault(); items[0].focus(); }
  });
})();
