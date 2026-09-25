import assert from "node:assert/strict";
import test from "node:test";

import {
  createPresenceMonitor,
  PRESENCE_STATES,
} from "../../app/static/presence-controller.mjs";
import {
  createLocalCamera,
  createLocalFaceDetector,
} from "../../app/static/presence-runtime.mjs";

const flush = () => new Promise((resolve) => setImmediate(resolve));

function createHarness({
  results = [],
  absenceMs = 15000,
  graceMs = 0,
  motion = false,
  detectorFactory,
  cameraFactory,
} = {}) {
  let currentTime = 0;
  let hidden = false;
  let frameId = 0;
  const frames = new Map();
  const cameras = [];
  const statuses = [];
  const advisories = [];
  const errors = [];
  let detectorCreates = 0;
  let cameraCreates = 0;
  let detectorCloses = 0;
  let resultIndex = 0;
  const shield = {
    locked: false,
    calls: [],
    isLocked() {
      return this.locked;
    },
    obscure(message, source) {
      this.calls.push({ message, source });
      this.locked = true;
    },
  };

  const defaultDetectorFactory = async () => ({
    detectForVideo() {
      const next = results[Math.min(resultIndex, Math.max(0, results.length - 1))] ?? false;
      resultIndex += 1;
      if (next instanceof Error) throw next;
      return { detections: next ? [{}] : [] };
    },
    close() {
      detectorCloses += 1;
    },
  });

  const defaultCameraFactory = async () => {
    const track = {
      stops: 0,
      ended: null,
      stop() { this.stops += 1; },
      addEventListener(name, callback) {
        if (name === "ended") this.ended = callback;
      },
      end() { this.ended?.(); },
    };
    const video = {
      currentTime: 0,
      srcObject: null,
      pauses: 0,
      pause() { this.pauses += 1; },
    };
    const camera = {
      stream: { getTracks: () => [track], getVideoTracks: () => [track] },
      video,
      track,
    };
    video.srcObject = camera.stream;
    cameras.push(camera);
    return camera;
  };

  const monitor = createPresenceMonitor({
    shield,
    createDetector: async () => {
      detectorCreates += 1;
      return (detectorFactory || defaultDetectorFactory)();
    },
    createCamera: async () => {
      cameraCreates += 1;
      const camera = await (cameraFactory || defaultCameraFactory)();
      if (!cameras.includes(camera)) cameras.push(camera);
      return camera;
    },
    motionCheck: () => motion,
    onStatus: (value) => statuses.push(value),
    onAdvisory: (value) => advisories.push(value),
    onError: (value) => errors.push(value),
    now: () => currentTime,
    requestFrame: (callback) => {
      frameId += 1;
      frames.set(frameId, callback);
      return frameId;
    },
    cancelFrame: (handle) => frames.delete(handle),
    isDocumentHidden: () => hidden,
    absenceMs,
    checkIntervalMs: 0,
    resumeGraceMs: graceMs,
  });

  return {
    monitor,
    shield,
    cameras,
    statuses,
    advisories,
    errors,
    counts: {
      get detectorCreates() { return detectorCreates; },
      get cameraCreates() { return cameraCreates; },
      get detectorCloses() { return detectorCloses; },
    },
    setHidden(value) {
      hidden = value;
    },
    setShielded(value) {
      shield.locked = value;
    },
    setTime(value) {
      currentTime = value;
    },
    async step(value) {
      currentTime = value;
      const activeCamera = cameras.at(-1);
      if (activeCamera) activeCamera.video.currentTime += 1;
      const entry = frames.entries().next().value;
      assert.ok(entry, "a frame callback should be scheduled");
      const [handle, callback] = entry;
      frames.delete(handle);
      callback(value);
      await flush();
    },
  };
}

test("does not initialize anything before officer opt-in", () => {
  const harness = createHarness();
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.OFF);
  assert.equal(harness.counts.detectorCreates, 0);
  assert.equal(harness.counts.cameraCreates, 0);
});

test("face presence and intermittent short absence never obscure", async () => {
  const present = createHarness({ results: [true, true, true] });
  await present.monitor.enable();
  await present.step(0);
  await present.step(16000);
  assert.equal(present.shield.calls.length, 0);

  const intermittent = createHarness({ results: [false, false, true, false, false] });
  await intermittent.monitor.enable();
  await intermittent.step(0);
  await intermittent.step(10000);
  await intermittent.step(14000);
  await intermittent.step(20000);
  await intermittent.step(34999);
  assert.equal(intermittent.shield.calls.length, 0);
});

test("continuous absence obscures exactly once and resume applies grace", async () => {
  const harness = createHarness({ results: [false], graceMs: 5000 });
  await harness.monitor.enable();
  await harness.step(4999);
  await harness.step(5000);
  await harness.step(19999);
  assert.equal(harness.shield.calls.length, 0);
  await harness.step(20000);
  assert.equal(harness.shield.calls.length, 1);
  assert.equal(harness.shield.calls[0].source, "workstation-presence");
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.SHIELDED);

  harness.setShielded(false);
  await harness.monitor.resume();
  await harness.step(24999);
  await harness.step(25000);
  await harness.step(39999);
  assert.equal(harness.shield.calls.length, 1);
  await harness.step(40000);
  assert.equal(harness.shield.calls.length, 2);
});

test("limited motion is advisory only", async () => {
  const harness = createHarness({ results: [true, true], motion: true });
  await harness.monitor.enable();
  await harness.step(0);
  await harness.step(20000);
  assert.equal(harness.shield.calls.length, 0);
  assert.ok(harness.advisories.some((message) => message.includes("Limited motion")));
});

test("repeated inference failure releases resources and becomes unavailable", async () => {
  const failure = new Error("inference failed");
  const harness = createHarness({ results: [failure, failure, failure] });
  await harness.monitor.enable();
  await harness.step(0);
  await harness.step(1);
  await harness.step(2);
  await flush();
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.UNAVAILABLE);
  assert.equal(harness.cameras[0].track.stops, 1);
  assert.equal(harness.counts.detectorCloses, 1);
  assert.equal(harness.errors.at(-1), failure);
  assert.equal(harness.shield.calls.length, 0);
});

test("camera failure closes the detector and remains non-blocking", async () => {
  const harness = createHarness({
    cameraFactory: async () => { throw new Error("permission denied"); },
  });
  await harness.monitor.enable();
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.UNAVAILABLE);
  assert.equal(harness.counts.detectorCloses, 1);
  assert.equal(harness.shield.calls.length, 0);
});

test("an ended camera track releases resources and becomes unavailable", async () => {
  const harness = createHarness({ results: [true] });
  await harness.monitor.enable();
  harness.cameras[0].track.end();
  await flush();
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.UNAVAILABLE);
  assert.equal(harness.cameras[0].track.stops, 1);
  assert.equal(harness.counts.detectorCloses, 1);
  assert.equal(harness.shield.calls.length, 0);
});

test("disable during model initialization closes the stale detector", async () => {
  let resolveDetector;
  let closes = 0;
  const deferred = new Promise((resolve) => { resolveDetector = resolve; });
  const harness = createHarness({ detectorFactory: () => deferred });
  const enabling = harness.monitor.enable();
  await flush();
  const disabling = harness.monitor.disable();
  resolveDetector({ detectForVideo() {}, close() { closes += 1; } });
  await Promise.all([enabling, disabling]);
  assert.equal(closes, 1);
  assert.equal(harness.counts.cameraCreates, 0);
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.OFF);
});

test("hidden, shielded, disabled and disposed states stop every camera track", async () => {
  const harness = createHarness({ results: [true] });
  await harness.monitor.enable();
  await harness.monitor.pause("hidden");
  assert.equal(harness.cameras[0].track.stops, 1);
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.PAUSED);

  await harness.monitor.resume();
  await harness.monitor.pause("shielded");
  assert.equal(harness.cameras[1].track.stops, 1);
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.SHIELDED);

  harness.setShielded(false);
  await harness.monitor.resume();
  await harness.monitor.disable();
  assert.equal(harness.cameras[2].track.stops, 1);
  assert.equal(harness.monitor.getState(), PRESENCE_STATES.OFF);

  await harness.monitor.enable();
  await harness.monitor.dispose();
  assert.equal(harness.cameras[3].track.stops, 1);
  assert.equal(harness.counts.detectorCloses, 2);
});

test("GPU detector failure retries once with CPU using same-origin assets", async () => {
  const delegates = [];
  let wasmPath = "";
  const detector = { close() {} };
  const result = await createLocalFaceDetector({
    secureContext: true,
    mediaDevices: { getUserMedia() {} },
    loadVision: async () => ({
      FilesetResolver: {
        async forVisionTasks(path) {
          wasmPath = path;
          return { path };
        },
      },
      FaceDetector: {
        async createFromOptions(_fileset, options) {
          delegates.push(options.baseOptions.delegate);
          assert.equal(
            options.baseOptions.modelAssetPath,
            "/static/vendor/mediapipe/blaze_face_short_range.tflite",
          );
          if (options.baseOptions.delegate === "GPU") throw new Error("GPU unavailable");
          return detector;
        },
      },
    }),
  });
  assert.equal(result, detector);
  assert.deepEqual(delegates, ["GPU", "CPU"]);
  assert.equal(wasmPath, "/static/vendor/mediapipe/wasm");
});

test("camera playback failure stops the acquired track", async () => {
  const track = { stops: 0, stop() { this.stops += 1; } };
  const stream = { getTracks: () => [track] };
  const video = { async play() { throw new Error("play failed"); } };
  await assert.rejects(
    createLocalCamera({
      mediaDevices: { async getUserMedia() { return stream; } },
      documentRef: { createElement: () => video },
    }),
    /play failed/,
  );
  assert.equal(track.stops, 1);
});
