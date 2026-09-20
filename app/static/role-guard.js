(() => {
  const root = document.documentElement;
  const role = root.dataset.activeRole;
  if (!role) return;
  let registry = {};
  try { registry = JSON.parse(document.body?.dataset?.roleViewRegistry || "{}"); } catch (_error) { registry = {}; }
  const classifiedPanels = new Set(
    Object.values(registry).flatMap((view) => Object.keys(view?.panels || {}))
  );
  document.querySelectorAll("[data-roles]").forEach((element) => {
    const roles = (element.dataset.roles || "").split(/\s+/).filter(Boolean);
    if (roles.length && !roles.includes(role)) element.remove();
  });
  document.querySelectorAll("[data-panel-id]").forEach((element) => {
    if (!classifiedPanels.has(element.dataset.panelId)) {
      element.dataset.roleClassification = "unclassified";
      console.warn(`TRINETRA role registry: unclassified panel ${element.dataset.panelId}`);
    }
  });
})();
