(() => {
  const page = document.body;
  if (!page.classList.contains("risk-page")) return;

  const form = document.querySelector("#risk-check-form");
  const input = document.querySelector("#risk-address");
  const hint = document.querySelector("#risk-hint");
  if (!form || !input || !hint) return;

  const defaultHint = hint.textContent.trim();
  const clearError = () => {
    input.classList.remove("is-error");
    hint.classList.remove("is-error");
    if (hint.dataset.clientError === "true") {
      hint.textContent = defaultHint;
      delete hint.dataset.clientError;
    }
  };

  document.addEventListener("click", (event) => {
    const sample = event.target.closest("[data-risk-address]");
    if (!sample) return;
    input.value = sample.dataset.riskAddress || "";
    clearError();
    form.requestSubmit();
  });

  input.addEventListener("input", clearError);

  form.addEventListener("submit", (event) => {
    const address = input.value.trim();
    input.value = address;
    if (address.length >= 26) return;
    event.preventDefault();
    input.classList.add("is-error");
    hint.classList.add("is-error");
    hint.dataset.clientError = "true";
    hint.textContent = "That does not look like a full address. Paste the complete recipient address.";
    input.focus();
  });
})();
