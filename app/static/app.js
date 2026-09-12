(() => {
  document.addEventListener("click", (event) => {
    const sample = event.target.closest("[data-complaint-sample]");
    if (!sample) return;
    const input = document.querySelector("#ack_no");
    if (!input) return;
    input.value = sample.dataset.complaintSample;
    input.focus();
  });

  window.trinetraCopy = async (value) => {
    if (!navigator.clipboard) return false;
    await navigator.clipboard.writeText(value);
    return true;
  };
})();
