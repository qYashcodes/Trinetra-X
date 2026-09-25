export const PRESENCE_STATES = Object.freeze({
  OFF: "off",
  STARTING: "starting",
  MONITORING: "monitoring",
  PAUSED: "paused",
  SHIELDED: "shielded",
  UNAVAILABLE: "unavailable",
});

const MAX_CONSECUTIVE_DETECTION_ERRORS = 3;

export function createPresenceMonitor({
  shield,
  createDetector,
  createCamera,
  motionCheck = () => false,
  resetMotion = () => {},
  onStatus = () => {},
  onAdvisory = () => {},
  onError = () => {},
  now = () => performance.now(),
  requestFrame = (callback) => requestAnimationFrame(callback),
  cancelFrame = (handle) => cancelAnimationFrame(handle),
  isDocumentHidden = () => document.hidden,
  absenceMs = 15000,
  checkIntervalMs = 400,
  resumeGraceMs = 5000,
}) {
  let state = PRESENCE_STATES.OFF;
  let preferenceEnabled = false;
  let detector = null;
  let camera = null;
  let scheduledHandle = null;
  let scheduledKind = null;
  let lastCheckAt = Number.NEGATIVE_INFINITY;
  let lastVideoTime = Number.NEGATIVE_INFINITY;
  let missingSince = null;
  let graceUntil = 0;
  let lockIssued = false;
  let consecutiveErrors = 0;
  let generation = 0;
  let startPromise = null;
  let disposed = false;

  const emitStatus = (nextState, detail) => {
    state = nextState;
    onStatus({ state, detail, enabled: preferenceEnabled });
  };

  const cancelScheduledFrame = () => {
    if (scheduledHandle === null) return;
    if (scheduledKind === "video" && camera?.video?.cancelVideoFrameCallback) {
      camera.video.cancelVideoFrameCallback(scheduledHandle);
    } else {
      cancelFrame(scheduledHandle);
    }
    scheduledHandle = null;
    scheduledKind = null;
  };

  const stopCameraResource = (activeCamera) => {
    if (!activeCamera) return;
    activeCamera.stream?.getTracks?.().forEach((track) => track.stop());
    activeCamera.video?.pause?.();
    if (activeCamera.video) activeCamera.video.srcObject = null;
  };

  const stopCamera = () => {
    cancelScheduledFrame();
    if (!camera) return;
    stopCameraResource(camera);
    camera = null;
    lastVideoTime = Number.NEGATIVE_INFINITY;
    resetMotion();
  };

  const closeDetector = async () => {
    const activeDetector = detector;
    detector = null;
    if (activeDetector?.close) await activeDetector.close();
  };

  const stopRuntime = async ({ closeModel }) => {
    generation += 1;
    stopCamera();
    missingSince = null;
    lockIssued = false;
    consecutiveErrors = 0;
    onAdvisory("");
    if (closeModel) await closeDetector();
  };

  const fail = async (error) => {
    await stopRuntime({ closeModel: true });
    emitStatus(PRESENCE_STATES.UNAVAILABLE, "Monitoring unavailable");
    onError(error);
  };

  const scheduleNextFrame = () => {
    if (state !== PRESENCE_STATES.MONITORING || !camera) return;
    if (typeof camera.video.requestVideoFrameCallback === "function") {
      scheduledKind = "video";
      scheduledHandle = camera.video.requestVideoFrameCallback(processFrame);
    } else {
      scheduledKind = "animation";
      scheduledHandle = requestFrame(processFrame);
    }
  };

  const watchForCameraFailure = (activeCamera) => {
    const tracks = activeCamera.stream?.getVideoTracks?.()
      || activeCamera.stream?.getTracks?.()
      || [];
    tracks.forEach((track) => {
      if (typeof track.addEventListener !== "function") return;
      track.addEventListener("ended", () => {
        if (camera === activeCamera && state === PRESENCE_STATES.MONITORING) {
          void fail(new Error("The active camera stream ended."));
        }
      }, { once: true });
    });
  };

  const processFrame = (timestamp) => {
    scheduledHandle = null;
    scheduledKind = null;
    if (state !== PRESENCE_STATES.MONITORING || !camera || !detector) return;
    if (shield.isLocked()) {
      void pause("shielded");
      return;
    }
    const observedAt = Number.isFinite(timestamp) ? timestamp : now();
    if (observedAt - lastCheckAt < checkIntervalMs) {
      scheduleNextFrame();
      return;
    }
    if (camera.video.currentTime === lastVideoTime) {
      scheduleNextFrame();
      return;
    }
    lastCheckAt = observedAt;
    lastVideoTime = camera.video.currentTime;

    let result;
    try {
      result = detector.detectForVideo(camera.video, observedAt);
      consecutiveErrors = 0;
    } catch (error) {
      consecutiveErrors += 1;
      if (consecutiveErrors >= MAX_CONSECUTIVE_DETECTION_ERRORS) {
        void fail(error);
        return;
      }
      scheduleNextFrame();
      return;
    }

    const facePresent = (result?.detections?.length ?? 0) > 0;
    const currentTime = now();
    if (facePresent) {
      missingSince = null;
      lockIssued = false;
      onAdvisory(
        motionCheck(camera.video)
          ? "Limited motion observed; presence monitoring remains active."
          : ""
      );
      scheduleNextFrame();
      return;
    }

    resetMotion();
    onAdvisory("");
    if (currentTime < graceUntil) {
      missingSince = null;
      scheduleNextFrame();
      return;
    }
    if (missingSince === null) missingSince = currentTime;
    if (!lockIssued && currentTime - missingSince >= absenceMs) {
      lockIssued = true;
      shield.obscure(
        "No face detected at the workstation — session display obscured.",
        "workstation-presence"
      );
      void pause("shielded");
      return;
    }
    scheduleNextFrame();
  };

  const start = async () => {
    if (disposed || !preferenceEnabled) return;
    if (shield.isLocked() || isDocumentHidden()) {
      emitStatus(PRESENCE_STATES.PAUSED, "Paused");
      return;
    }
    if (state === PRESENCE_STATES.MONITORING && camera) return;
    if (startPromise) {
      await startPromise;
      if (
        !disposed
        && preferenceEnabled
        && state !== PRESENCE_STATES.MONITORING
        && !shield.isLocked()
        && !isDocumentHidden()
      ) {
        return start();
      }
      return;
    }

    const startGeneration = ++generation;
    emitStatus(PRESENCE_STATES.STARTING, "Starting");
    const pending = (async () => {
      let candidateDetector = detector;
      let candidateCamera = null;
      let createdDetector = false;
      try {
        if (!candidateDetector) {
          candidateDetector = await createDetector();
          createdDetector = true;
        }
        if (disposed || !preferenceEnabled || startGeneration !== generation) {
          if (createdDetector && candidateDetector?.close) await candidateDetector.close();
          return;
        }
        detector = candidateDetector;
        candidateCamera = await createCamera();
        if (disposed || !preferenceEnabled || startGeneration !== generation) {
          stopCameraResource(candidateCamera);
          if (createdDetector && detector === candidateDetector) await closeDetector();
          return;
        }
        camera = candidateCamera;
        watchForCameraFailure(camera);
        lastCheckAt = Number.NEGATIVE_INFINITY;
        lastVideoTime = Number.NEGATIVE_INFINITY;
        missingSince = null;
        lockIssued = false;
        consecutiveErrors = 0;
        emitStatus(PRESENCE_STATES.MONITORING, "Monitoring");
        scheduleNextFrame();
      } catch (error) {
        if (startGeneration === generation && !disposed) await fail(error);
      }
    })();
    startPromise = pending;
    try {
      await pending;
    } finally {
      if (startPromise === pending) startPromise = null;
    }
  };

  const enable = async () => {
    preferenceEnabled = true;
    graceUntil = now() + resumeGraceMs;
    await start();
  };

  const disable = async () => {
    preferenceEnabled = false;
    await stopRuntime({ closeModel: true });
    emitStatus(PRESENCE_STATES.OFF, "Off");
  };

  const pause = async (reason = "hidden") => {
    if (!preferenceEnabled || disposed) return;
    generation += 1;
    stopCamera();
    emitStatus(
      reason === "shielded" ? PRESENCE_STATES.SHIELDED : PRESENCE_STATES.PAUSED,
      reason === "shielded" ? "Workspace obscured" : "Paused"
    );
  };

  const resume = async () => {
    if (!preferenceEnabled || disposed) return;
    graceUntil = now() + resumeGraceMs;
    missingSince = null;
    lockIssued = false;
    await start();
  };

  const retry = async () => {
    if (!preferenceEnabled || disposed) return;
    await stopRuntime({ closeModel: true });
    graceUntil = now() + resumeGraceMs;
    await start();
  };

  const dispose = async () => {
    disposed = true;
    preferenceEnabled = false;
    await stopRuntime({ closeModel: true });
  };

  return {
    enable,
    disable,
    pause,
    resume,
    retry,
    dispose,
    getState: () => state,
    isEnabled: () => preferenceEnabled,
  };
}
