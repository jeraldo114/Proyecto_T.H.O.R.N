import assert from 'node:assert/strict';
import test from 'node:test';
import { WakeWordGate } from '../src/wake-word.js';
import { ConversationLoop } from '../src/conversation.js';

test('el nombre exige una palabra inicial completa y permite dictado latino', () => {
  let now = 0;
  const gate = new WakeWordGate({ clock: () => now, followUpMs: 30000 });
  for (const text of ['tornado peligroso', 'el motor está activo', 'no tor', 'hablamos de Thorn ayer'])
    assert.deepEqual(gate.consume(text), { accepted: false, activated: false, text: '' });
  for (const text of ['Thorn, revisa la memoria', 'oye Tor revisa la memoria', 'hola Torn, revisa la memoria', 'T.H.O.R.N, revisa la memoria'])
    assert.equal(gate.consume(text).text, 'revisa la memoria');
  assert.equal(gate.consume('y el proyecto anterior').accepted, true);
  now = 30001; assert.equal(gate.consume('una charla ambiental').accepted, false);
  gate.consume('Thorn'); gate.reset(); assert.equal(gate.open, false);
});

test('frases ambientales no llegan al modelo, la pantalla ni la memoria del chat', async () => {
  const calls = [], transcripts = ['una charla ambiental', 'Thorn', 'cómo va mi proyecto'];
  const loop = new ConversationLoop({
    createCapture: () => ({ start: async () => {}, pause: async () => {}, stop() {} }),
    transcribe: async () => transcripts.shift(), ask: async text => { calls.push(['ask', text]); return 'listo'; },
    speak: async () => {}, onState() {}, onLevel() {}, onText: value => calls.push(['text', value]),
  });
  loop.requireWake = true; await loop.start();
  assert.equal(loop.state, 'waiting_for_wake');
  await loop.turn(new Uint8Array(6400), loop.revision); assert.deepEqual(calls, []);
  await loop.turn(new Uint8Array(6400), loop.revision); assert.deepEqual(calls, [['text', '']]);
  await loop.turn(new Uint8Array(6400), loop.revision);
  assert.deepEqual(calls.at(-1), ['ask', 'cómo va mi proyecto']); loop.stop();
});
