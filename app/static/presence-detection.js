import { createPresenceMonitor, PRESENCE_STATES } from "/static/presence-controller.mjs";
import {
  createLocalCamera,
  createLocalFaceDetector,
  createLuminanceMotionObserver,
} from "/static/presence-runtime.mjs";

const controls = document.querySelector("[data-presence-controls]");

if (controls) {
  const toggle = controls.querySelector("[data-presence-toggle]");
  const retry = controls.querySelector("[data-presence-retry]");
  const status = controls.querySelector("[data-presence-status]");
  const detail = controls.querySelector("[data-presence-detail]");
  const csrf = document.body?.dataset?.csrfToken || "";
  const defaultDetail = "On-device face presence only; not identity or liveness verification.";
  const stateLabels = {
    [PRESENCE_STATES.OFF]: "Off",
    [PRESENCE_STATES.STARTING]: "Starting",
    [PRESENCE_STATES.MONITORING]: "Monitoring",
    [PRESENCE_STATES.PAUSED]: "Paused",
    [PRESENCE_STATES.SHIELDED]: "Paused",
    [PRESENCE_STATES.UNAVAILABLE]: "Unavailable",
  };
  let monitor = null;
  let advisory = "";
  let failureDetail = "";

  const waitForShield = async () => {
    if (window.trinetraSessionShield) return window.trinetraSessionShield;
    await new Promise((resolve) => {
      const timeout = window.setTimeout(resolve, 1000);
      window.addEventListener("trinetra:session-shield-ready", () => {
        window.clearTimeout(timeout);
        resolve();
      }, { once: true });
    });
    return window.trinetraSessionShield || null;
  };

  const updateUi = ({ state, enabled }) => {
    controls.dataset.presenceState = state;
    if (status) status.textContent = stateLabels[state] || "Unavailable";
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(enabled));
      toggle.textContent = enabled ? "Disable presence" : "Enable presence";
    }
    if (retry) retry.hidden = state !== PRESENCE_STATES.UNAVAILABLE;
    if (detail) {
      detail.textContent = failureDetail || advisory || defaultDetail;
    }
  };

  const startBootstrap = async () => {
    const shield = await waitForShield();
    if (!shield) {
      failureDetail = "Session privacy shield unavailable; monitoring was not started.";
      updateUi({ state: PRESENCE_STATES.UNAVAILABLE, enabled: false });
      return;
    }

    const motion = createLuminanceMotionObserver();

    monitor = createPresenceMonitor({
      shield,
      createDetector: () => createLocalFaceDetector(),
      createCamera: () => createLocalCamera(),
      motionCheck: (video) => motion.check(video),
      resetMotion: () => motion.reset(),
      absenceMs: Number(controls.dataset.presenceAbsenceMs || 15000),
      checkIntervalMs: Number(controls.dataset.presenceCheckIntervalMs || 400),
      resumeGraceMs: Number(controls.dataset.presenceResumeGraceMs || 5000),
      onStatus: ({ state, enabled }) => {
        if (state !== PRESENCE_STATES.UNAVAILABLE) failureDetail = "";
        updateUi({ state, enabled });
      },
      onAdvisory: (message) => {
        advisory = message;
        updateUi({ state: monitor.getState(), enabled: monitor.isEnabled() });
      },
      onError: (error) => {
        console.warn("TRINETRA workstation presence unavailable.", error);
        failureDetail = "Camera or local detector unavailable; the existing idle shield remains active.";
        updateUi({ state: PRESENCE_STATES.UNAVAILABLE, enabled: monitor.isEnabled() });
      },
    });

    const persistPreference = async (enabled) => {
      const response = await fetch("/api/session/presence", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok) throw new Error(`Presence preference update failed (${response.status})`);
      return response.json();
    };

    toggle?.addEventListener("click", async () => {
      const enabled = !monitor.isEnabled();
      toggle.disabled = true;
      failureDetail = "";
      try {
        await persistPreference(enabled);
        if (enabled) await monitor.enable();
        else await monitor.disable();
      } catch (error) {
        console.warn("TRINETRA presence preference was not changed.", error);
        failureDetail = "Preference could not be saved; monitoring state was not changed.";
        updateUi({ state: monitor.getState(), enabled: monitor.isEnabled() });
      } finally {
        toggle.disabled = false;
      }
    });

    retry?.addEventListener("click", async () => {
      retry.disabled = true;
      failureDetail = "";
      try {
        await monitor.retry();
      } finally {
        retry.disabled = false;
      }
    });

    const shieldListener = (event) => {
      if (event.detail?.locked) void monitor.pause("shielded");
      else void monitor.resume();
    };
    const visibilityListener = () => {
      if (document.hidden) void monitor.pause("hidden");
      else void monitor.resume();
    };
    window.addEventListener("trinetra:session-shield-change", shieldListener);
    document.addEventListener("visibilitychange", visibilityListener);
    window.addEventListener("pagehide", () => {
      window.removeEventListener("trinetra:session-shield-change", shieldListener);
      document.removeEventListener("visibilitychange", visibilityListener);
      void monitor.dispose();
    }, { once: true });

    if (controls.dataset.presenceEnabled === "true") await monitor.enable();
    else updateUi({ state: PRESENCE_STATES.OFF, enabled: false });
  };

  void startBootstrap();
}
