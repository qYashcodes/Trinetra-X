# MediaPipe face-detection runtime

This directory contains the self-hosted browser runtime needed by TRINETRA's proposed
workstation-presence feature. The files were supplied as a matched set by the project team.
The JavaScript bundle identifies itself as `@mediapipe/tasks-vision` version `1.0.1`.

Upstream package: <https://www.npmjs.com/package/@mediapipe/tasks-vision/v/1.0.1>

Model family: BlazeFace short-range, float16, version 1:
<https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite>

The application must load the files locally. Do not replace these with CDN references; TRINETRA's
frontend assets are intentionally vendored. Keep the bundle, both loader/binary pairs, and the model
versioned together. The `vision_wasm_module_internal.*` pair from the supplied archive is omitted
because the proposed integration calls `FilesetResolver.forVisionTasks(path)` in its default mode,
which selects only the SIMD or non-SIMD pair below.

## Installed file hashes (SHA-256)

| File | SHA-256 |
| --- | --- |
| `vision_bundle.mjs` | `D885630C297C0B20B1FE86096CB06291C4C8080876F27852E724F24AC603713F` |
| `blaze_face_short_range.tflite` | `B4578F35940BF5A1A655214A1CCE5CAB13EBA73C1297CD78E1A04C2380B0152F` |
| `wasm/vision_wasm_internal.js` | `E170EE67DD4E16C1A6FCD8840A206687E5A59B22C20E4A902BC445B095454D73` |
| `wasm/vision_wasm_internal.wasm` | `8DA277A733926EACD0474B8704B36742D6EC3231C57A860C5B889DFF8F1DF886` |
| `wasm/vision_wasm_nosimd_internal.js` | `E81D715A3D42CC3373602EB2F7AFF795D164934DB680E32496B65DAB537F9658` |
| `wasm/vision_wasm_nosimd_internal.wasm` | `A28483CD42E74E855BF5EBDB6B40D9B66A5B49E35E95020BC97669E6822A3192` |

The npm package and model card declare the Apache-2.0 license. The complete license text and
attribution/provenance notice are included in `LICENSE.apache-2.0.txt` and
`THIRD_PARTY_NOTICES.md`.
