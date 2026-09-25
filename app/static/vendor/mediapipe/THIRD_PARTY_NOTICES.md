# MediaPipe third-party notices

TRINETRA vendors an unmodified subset of `@mediapipe/tasks-vision` version `1.0.1` and the
MediaPipe BlazeFace short-range float16 model (version 1) for optional, on-device face-presence
detection.

Copyright The MediaPipe Authors and Google LLC contributors.

The npm package metadata declares `Apache-2.0`. The BlazeFace short-range model card states that
the model is licensed under the Apache License, Version 2.0. A complete copy of that license is in
[`LICENSE.apache-2.0.txt`](LICENSE.apache-2.0.txt).

Upstream sources and documentation:

- MediaPipe repository and license: <https://github.com/google-ai-edge/mediapipe>
- Package: <https://www.npmjs.com/package/@mediapipe/tasks-vision/v/1.0.1>
- Model binary: <https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite>
- BlazeFace short-range model card: <https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Short%20Range%29.pdf>

The vendored files are not modified. TRINETRA's integration code in `presence-controller.mjs`,
`presence-runtime.mjs`, and `presence-detection.js` is separate application code.
