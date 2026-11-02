import test from 'node:test';
import assert from 'node:assert/strict';
import { ConversationLoop } from '../src/conversation.js';

const settle = () => new Promise(resolve => setImmediate(resolve));
function harness(overrides = {}) {
  const calls = [], capture = { start: async () => { calls.push('open'); }, pause: async () => { calls.push('close'); }, stop: () => { calls.push('stop'); } };
  let callbacks;
  const loop = new ConversationLoop({
    createCapture: value => { callbacks = value; return capture; },
    transcribe: async () => { calls.push('transcribe'); return 'consulta'; },
    ask: async () => { calls.push('ask'); return 'respuesta'; },
    speak: async () => { calls.push('speak'); }, onState: () => {}, onLevel: () => {}, onText: () => {}, ...overrides,
  });
  return { loop, calls, capture, get callbacks() { return callbacks; } };
}

test('no abre entrada al construir y serializa cierre, STT, IA, TTS y reapertura', async () => {
  const h = harness(); assert.deepEqual(h.calls, []);
  await h.loop.start();
  const pcm = new Uint8Array(6400).fill(1);
  await h.loop.turn(pcm, h.loop.revision);
  assert.deepEqual(h.calls, ['open', 'close', 'transcribe', 'ask', 'speak', 'open']);
  assert(pcm.every(value => value === 0)); assert.equal(h.loop.state, 'listening');
  h.loop.stop();
});

test('detener durante respuesta tardia no habla ni reabre entrada', async () => {
  let reply;
  const h = harness({ ask: () => new Promise(resolve => { reply = resolve; }) });
  await h.loop.start();
  const pcm = new Uint8Array(6400).fill(1), pending = h.loop.turn(pcm, h.loop.revision);
  await settle(); h.loop.stop(); reply('respuesta tardia'); await pending;
  assert.equal(h.loop.enabled, false); assert(!h.calls.includes('speak'));
  assert.equal(h.calls.filter(value => value === 'open').length, 1);
  assert(pcm.every(value => value === 0));
});

test('el silencio no consulta ni sintetiza y los turnos concurrentes se descartan', async () => {
  let decode;
  const h = harness({ transcribe: () => new Promise(resolve => { decode = resolve; }) });
  await h.loop.start(); const pending = h.loop.turn(new Uint8Array(6400), h.loop.revision);
  await settle(); const duplicate = new Uint8Array(6400).fill(1);
  await h.loop.turn(duplicate, h.loop.revision); assert(duplicate.every(value => !value));
  decode(''); await pending;
  assert(!h.calls.includes('ask') && !h.calls.includes('speak')); h.loop.stop();
});

test('permiso rechazado detiene el modo sin reintentos', async () => {
  const h = harness(); h.capture.start = async () => { throw new DOMException('denied', 'NotAllowedError'); };
  await h.loop.start(); assert.equal(h.loop.state, 'permission_required'); assert.equal(h.loop.enabled, false);
  assert.deepEqual(h.calls, ['stop']);
});

test('un fallo local detiene el dispositivo y borra el buffer sin enviar a IA', async () => {
  const h = harness({ transcribe: async () => { throw new Error('failed'); } });
  await h.loop.start(); const pcm = new Uint8Array(6400).fill(1);
  await h.loop.turn(pcm, h.loop.revision);
  assert.equal(h.loop.state, 'turn_error'); assert(!h.calls.includes('ask')); assert(pcm.every(value => !value));
});

test('cancelar permiso pendiente limpia tambien un dispositivo abierto tarde', async () => {
  let grant;
  const h = harness(); h.capture.start = () => new Promise(resolve => { grant = resolve; });
  const pending = h.loop.start(); h.loop.stop(); grant(); await pending;
  assert.equal(h.loop.state, 'off'); assert.equal(h.calls.filter(value => value === 'stop').length, 2);
});
