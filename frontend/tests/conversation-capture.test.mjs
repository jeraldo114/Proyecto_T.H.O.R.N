import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { MicVAD } from '@ricky0123/vad-web/dist/real-time-vad.js';
import { ConversationCapture, toPcm16, MAX_SAMPLES } from '../src/conversation-vad.js';

const originalNew = MicVAD.new, originalContext = globalThis.AudioContext;
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
let stream, options, released, node;
beforeEach(() => {
  released = 0;
  stream = { getTracks: () => [{ stop() { stream.stopped = true; } }], getAudioTracks: () => [{ addEventListener() {} }] };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => stream } });
  globalThis.AudioContext = class { resume() { return Promise.resolve(); } close() { this.closed = true; return Promise.resolve(); } createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; } };
  MicVAD.new = async value => {
    options = value;
    node = { connect() {}, port: { onmessage: async () => {} } };
    return { initializationState: 'uninitialized', model: { release: async () => { released++; } },
      async start() { await value.getStream(); this.initializationState = 'initialized'; },
      getAudioInstances() { return { vadNode: node }; },
      async pause() { await value.pauseStream(stream); }, async destroy() { await value.pauseStream(stream); released++; } };
  };
});
afterEach(() => {
  MicVAD.new = originalNew; globalThis.AudioContext = originalContext;
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia); else delete navigator.mediaDevices;
});
const callbacks = { onLevel() {}, onSpeechStart() {}, onSpeechEnd(pcm) { pcm.fill(0); }, onEnded() {} };

test('PCM mono LE satura extremos y nunca supera los 15 segundos', () => {
  const audio = new Float32Array(MAX_SAMPLES + 500); audio.set([-2, 0, 2]);
  const pcm = toPcm16(audio), view = new DataView(pcm.buffer);
  assert.equal(pcm.byteLength, 480000);
  assert.deepEqual([view.getInt16(0, true), view.getInt16(2, true), view.getInt16(4, true)], [-32768, 0, 32767]);
});
test('solo usa assets locales y pausa cerrando tracks antes de otro turno', async () => {
  const capture = new ConversationCapture(callbacks); await capture.start();
  assert.equal(options.baseAssetPath, '/hud/assets/'); assert.equal(options.startOnLoad, false);
  const ort = { env: { wasm: {} } }; options.ortConfig(ort);
  assert.equal(ort.env.wasm.proxy, true); assert.equal(ort.env.wasm.numThreads, 1);
  await capture.pause(); assert(stream.stopped); assert.equal(capture.accepting, false);
  capture.stop(); await Promise.resolve(); assert.equal(released, 1);
});
test('cancelar permiso pendiente cierra el stream tardio y libera el modelo', async () => {
  let grant;
  navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const capture = new ConversationCapture(callbacks), pending = capture.start();
  while (!grant) await new Promise(resolve => setImmediate(resolve));
  capture.stop(); grant(stream); await assert.rejects(pending);
  assert(stream.stopped); assert.equal(released, 1); assert.equal(capture.accepting, false);
});
test('voz tardia despues de detener no genera un nuevo turno y limpia samples', async () => {
  let turns = 0;
  const capture = new ConversationCapture({ ...callbacks, onSpeechEnd: () => { turns++; } });
  await capture.start(); capture.stop();
  const audio = new Float32Array(3200).fill(0.1); options.onSpeechEnd(audio);
  assert.equal(turns, 0); assert(audio.every(value => value === 0));
});

test('los frames se procesan en orden y un fallo de inferencia cierra captura', async () => {
  let failed = false;
  const capture = new ConversationCapture({ ...callbacks, onEnded: state => { failed = state === 'input_error'; } });
  capture.accepting = true;
  capture.frames = [1, 2, 3]; const processed = [];
  await capture.drainFrames(async value => { processed.push(value); await Promise.resolve(); });
  assert.deepEqual(processed, [1, 2, 3]);
  capture.frames = [{ data: { data: new ArrayBuffer(2048) } }];
  await capture.drainFrames(async () => { throw new Error('inference_failed'); });
  assert(failed); assert(capture.closed); assert.equal(capture.frames.length, 0);
});
