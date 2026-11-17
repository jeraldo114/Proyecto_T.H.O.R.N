import test from 'node:test';
import assert from 'node:assert/strict';
import { ConversationLoop } from '../src/voice/conversation.js';

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

test('corrige un turno sin mandar el prefijo al modelo', async () => {
  const heard = [], sent = [];
  const h = harness({ transcribe: async () => 'No, quise decir abre mi memoria',
    onText: value => heard.push(value), ask: async value => { sent.push(value); return ''; } });
  await h.loop.start();
  await h.loop.turn(new Uint8Array(6400), h.loop.revision);
  assert.deepEqual(heard, ['abre mi memoria']);
  assert.deepEqual(sent, heard);
  h.loop.stop();
});

test('durante la respuesta «espera» pausa, el eco se ignora y otra frase reemplaza', async () => {
  let phrase = 'Tor espera', interrupted = 0;
  const replaced = [];
  const h = harness({ transcribe: async () => phrase, onInterrupt: () => interrupted++,
    onReplace: value => replaced.push(value), spokenText: () => 'La CPU está al 12 % y la memoria al 40 %.' });
  await h.loop.start();
  h.loop.interruptOnly = true;
  await h.loop.interrupt(new Uint8Array(6400), h.loop.revision);
  assert.equal(interrupted, 1);
  phrase = 'la memoria al cuarenta';
  h.loop.duck(true);
  await h.loop.interrupt(new Uint8Array(6400), h.loop.revision);
  assert.deepEqual(replaced, []); assert.equal(h.loop.ducked, false, 'el eco devuelve el volumen');
  phrase = 'mejor dime la hora';
  await h.loop.interrupt(new Uint8Array(6400), h.loop.revision);
  assert.deepEqual(replaced, ['mejor dime la hora']);
  assert.equal(h.loop.pendingText, 'mejor dime la hora');
  assert(!h.calls.includes('ask'));
  h.loop.stop();
});

test('la escucha de interrupción se rearma tras cada frase oída mientras habla', async () => {
  const transcripts = ['¿qué tiempo hace?', 'mmm', 'eh'];
  let speaking, spoken = 0;
  const h = harness({ transcribe: async () => transcripts.shift(),
    speak: () => { spoken++; return new Promise(resolve => { speaking = resolve; }); } });
  h.capture.rearm = () => { h.calls.push('rearm'); return true; };
  h.loop.bargeIn = true;
  await h.loop.start();
  const turn = h.loop.turn(new Uint8Array(6400), h.loop.revision);
  for (let i = 0; i < 20 && !speaking; i++) await settle();
  assert.equal(h.loop.interruptOnly, true);
  // Dos ruidos seguidos: antes la escucha quedaba sorda tras el primero.
  h.callbacks.onSpeechEnd(new Uint8Array(6400)); await settle(); await settle();
  h.callbacks.onSpeechEnd(new Uint8Array(6400)); await settle(); await settle();
  assert.equal(h.calls.filter(value => value === 'rearm').length, 2);
  speaking(); await turn;
  assert.equal(spoken, 1); assert.equal(h.loop.interruptOnly, false);
  h.loop.stop();
});

test('una orden encima de la respuesta corta esa respuesta y atiende la nueva', async () => {
  const asked = [];
  let release;
  const h = harness({
    transcribe: async () => (asked.length ? 'mejor abre el sistema' : 'cuéntame algo largo'),
    ask: (prompt, signal) => {
      asked.push(prompt);
      if (asked.length > 1) return Promise.resolve('');
      return new Promise((resolve, reject) => { release = resolve; signal.addEventListener('abort', () => reject(new Error('aborted'))); });
    },
  });
  h.capture.rearm = () => true;
  h.loop.bargeIn = true;
  await h.loop.start();
  const turn = h.loop.turn(new Uint8Array(6400), h.loop.revision);
  for (let i = 0; i < 20 && !release; i++) await settle();
  h.callbacks.onSpeechStart();
  assert.equal(h.loop.ducked, true, 'baja la voz al primer indicio de voz');
  h.callbacks.onSpeechEnd(new Uint8Array(6400));
  await turn;
  assert.deepEqual(asked, ['cuéntame algo largo', 'mejor abre el sistema']);
  assert.equal(h.loop.ducked, false); assert.equal(h.loop.state, 'listening');
  h.loop.stop();
});

test('con nombre requerido pide al servidor detectarlo y respeta su decisión', async () => {
  const options = [], sent = [];
  const results = [{ text: '', wake: false }, { text: 'abre el sistema', wake: true }, { text: 'y la red', wake: null }];
  const h = harness({ transcribe: async (pcm, signal, value) => { options.push(value); return results.shift(); },
    ask: async value => { sent.push(value); return ''; } });
  h.loop.requireWake = true;
  await h.loop.start();
  await h.loop.turn(new Uint8Array(6400), h.loop.revision);
  assert.deepEqual(sent, []); assert.equal(h.loop.state, 'waiting_for_wake');
  await h.loop.turn(new Uint8Array(6400), h.loop.revision);
  await h.loop.turn(new Uint8Array(6400), h.loop.revision);
  assert.deepEqual(sent, ['abre el sistema', 'y la red']);
  assert.deepEqual(options.map(item => item.wake), [true, true, false], 'dentro del seguimiento ya no exige nombre');
  h.loop.stop();
});

test('tras fallar la consulta vuelve a escuchar sin reenviar el turno', async () => {
  let attempts = 0;
  const h = harness({ ask: async () => { attempts++; throw new Error('offline'); } });
  await h.loop.start();
  await h.loop.turn(new Uint8Array(6400), h.loop.revision);
  assert.equal(attempts, 1);
  assert.equal(h.loop.state, 'listening');
  assert(!h.calls.includes('speak'));
  h.loop.stop();
});
