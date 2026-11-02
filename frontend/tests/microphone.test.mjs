import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { PcmMicrophone } from '../src/microphone.js';

test('Worklet produce PCM little-endian correcto y no reproduce entrada', async () => {
  const source = await readFile(new URL('../src/microphone-worklet.js', import.meta.url), 'utf8');
  let Processor;
  const messages = [];
  vm.runInNewContext(source, {
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: value => messages.push(value) }; } },
    registerProcessor: (name, value) => { assert.equal(name, 'thorn-pcm-capture'); Processor = value; },
    Float32Array, ArrayBuffer, DataView, Math,
  });
  const capture = new Processor();
  capture.process([[new Float32Array([-1, -0.5, 0, 0.5, 1])]]);
  capture.port.onmessage({ data: { type: 'flush' } });
  assert.equal(messages.length, 2);
  const view = new DataView(messages[0].pcm);
  assert.deepEqual(Array.from({ length: 5 }, (_, i) => view.getInt16(i * 2, true)), [-32768, -16384, 0, 16384, 32767]);
  assert(messages[0].level > 0);
  assert.equal(messages[1].type, 'flushed');
  assert.equal(capture.process([]), false);
  assert.equal(capture.used, 0);
});

test('Cancelar permiso pendiente cierra el stream tardio sin crear AudioContext', async () => {
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  let grant, stops = 0, contexts = 0;
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    isSecureContext: true, AudioContext: class { constructor() { contexts++; } }, AudioWorkletNode: class {},
  } });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise(resolve => { grant = resolve; }),
  } } });
  try {
    const capture = new PcmMicrophone({ onLevel: () => {}, onLimit: () => {}, onEnded: () => {} });
    const request = capture.start();
    capture.cancel();
    grant({ getTracks: () => [{ stop: () => { stops++; } }] });
    await assert.rejects(request, /microphone_cancelled/);
    assert.equal(stops, 1); assert.equal(contexts, 0); assert.equal(capture.chunks.length, 0);
  } finally {
    if (oldWindow) Object.defineProperty(globalThis, 'window', oldWindow); else delete globalThis.window;
    if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator); else delete globalThis.navigator;
  }
});
