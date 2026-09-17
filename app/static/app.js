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

  const showShield = (warning) => {
    locked = true;
    shield.hidden = false;
    document.body.classList.add("session-obscured");
    if (message) {
      message.textContent = warning
        ? "This session is about to end because the workstation has been idle."
        : "Re-enter your active session to reveal case information.";
    }
    if (countdown) countdown.hidden = !warning;
  };

  const hideShield = () => {
    locked = false;
    shield.hidden = true;
    document.body.classList.remove("session-obscured");
    if (countdown) countdown.hidden = true;
  };

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
      showShield(true);
      if (countdown) {
        countdown.textContent = `Session ends in ${Math.max(1, Math.ceil((timeoutMs - idleMs) / 1000))} seconds`;
      }
      return;
    }
    if (idleMs >= obscureMs && !locked) showShield(false);
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
