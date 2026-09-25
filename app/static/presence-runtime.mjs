const VISION_MODULE_PATH = "/static/vendor/mediapipe/vision_bundle.mjs";
const VISION_WASM_PATH = "/static/vendor/mediapipe/wasm";
const FACE_MODEL_PATH = "/static/vendor/mediapipe/blaze_face_short_range.tflite";

export async function createLocalFaceDetector({
  secureContext = window.isSecureContext,
  mediaDevices = navigator.mediaDevices,
  loadVision = () => import(VISION_MODULE_PATH),
} = {}) {
  if (!secureContext || !mediaDevices?.getUserMedia) {
    throw new Error("A secure browser context and camera support are required.");
  }

  const { FaceDetector, FilesetResolver } = await loadVision();
  const fileset = await FilesetResolver.forVisionTasks(VISION_WASM_PATH);
  const options = {
    baseOptions: {
      modelAssetPath: FACE_MODEL_PATH,
      delegate: "GPU",
    },
    runningMode: "VIDEO",
  };
  try {
    return await FaceDetector.createFromOptions(fileset, options);
  } catch (_gpuError) {
    return FaceDetector.createFromOptions(fileset, {
      ...options,
      baseOptions: { ...options.baseOptions, delegate: "CPU" },
    });
  }
}

export async function createLocalCamera({
  mediaDevices = navigator.mediaDevices,
  documentRef = document,
  videoElement = null,
} = {}) {
  const stream = await mediaDevices.getUserMedia({
    video: { width: { ideal: 320 }, height: { ideal: 240 }, facingMode: "user" },
    audio: false,
  });
  const video = videoElement || documentRef.createElement("video");
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  video.autoplay = true;
  try {
    await video.play();
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
  return { stream, video };
}

export function createLuminanceMotionObserver({ documentRef = document } = {}) {
  const canvas = documentRef.createElement("canvas");
  canvas.width = 32;
  canvas.height = 32;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  let previous = null;
  let stillFrames = 0;

  return {
    check(video) {
      if (!context || video.readyState < 2) return false;
      try {
        context.drawImage(video, 0, 0, 32, 32);
        const frame = context.getImageData(0, 0, 32, 32).data;
        if (previous) {
          let difference = 0;
          for (let index = 0; index < frame.length; index += 4) {
            const currentLuma = (
              299 * frame[index]
              + 587 * frame[index + 1]
              + 114 * frame[index + 2]
            ) / 1000;
            const previousLuma = (
              299 * previous[index]
              + 587 * previous[index + 1]
              + 114 * previous[index + 2]
            ) / 1000;
            difference += Math.abs(currentLuma - previousLuma);
          }
          const averageDifference = difference / (frame.length / 4);
          stillFrames = averageDifference < 1.5 ? stillFrames + 1 : 0;
        }
        previous = frame;
        return stillFrames >= 12;
      } catch (_error) {
        return false;
      }
    },
    reset() {
      previous = null;
      stillFrames = 0;
      context?.clearRect(0, 0, canvas.width, canvas.height);
    },
  };
}
