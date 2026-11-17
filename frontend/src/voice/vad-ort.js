import * as ort from '/hud/assets/ort.wasm.min.mjs';
import { preserveTensorInputs } from './vad-ort-session.js';
export * from '/hud/assets/ort.wasm.min.mjs';
// El modelo permanece intacto al transferir sus entradas al worker ONNX.
export const InferenceSession = {
  async create(...args) { return preserveTensorInputs(await ort.InferenceSession.create(...args), ort.Tensor); },
};
