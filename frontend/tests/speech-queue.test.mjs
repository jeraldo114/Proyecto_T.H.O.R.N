import assert from 'node:assert/strict';
import test from 'node:test';
import { SpeechQueue, splitSpeech, takeSentence } from '../src/voice/speech-queue.js';

const settle = () => new Promise(resolve => setImmediate(resolve));

test('corta frases sin romper decimales, abreviaturas ni frases cortas', () => {
  assert.deepEqual(splitSpeech('La CPU está al 12.5 %. La memoria, al 40 %. Ok. Todo sigue estable por ahora.'),
    ['La CPU está al 12.5 %.', 'La memoria, al 40 %.', 'Ok. Todo sigue estable por ahora.']);
  assert.deepEqual(splitSpeech('Lo dijo el Dr. Pérez en su informe anual. Fin del resumen breve.'),
    ['Lo dijo el Dr. Pérez en su informe anual.', 'Fin del resumen breve.']);
  // Resumen de inicio real: los dos puntos no cortan una cifra de su etiqueta.
  assert.deepEqual(splitSpeech('Bienvenido. Son las 17:45. Bastion 9 esta listo. CPU: 31.5 %. Memoria: 71 %.'),
    ['Bienvenido. Son las 17:45.', 'Bastion 9 esta listo.', 'CPU: 31.5 %. Memoria: 71 %.']);
  assert.equal(takeSentence('Una frase sin terminar todavía'), null);
  const long = 'palabra, '.repeat(40);
  assert(takeSentence(long).sentence.length <= 221);
});

test('sintetiza la siguiente frase mientras suena la actual, en orden', async () => {
  const log = [];
  let finishPlayback;
  const queue = new SpeechQueue({
    synthesize: async text => { log.push(`synth:${text}`); return text; },
    play: blob => { log.push(`play:${blob}`); return new Promise(resolve => { finishPlayback = () => resolve('ended'); }); },
  });
  queue.push('uno'); queue.push('dos'); queue.push('tres');
  for (let i = 0; i < 6; i++) await settle();
  assert.deepEqual(log, ['synth:uno', 'play:uno', 'synth:dos', 'synth:tres']);
  finishPlayback(); for (let i = 0; i < 4; i++) await settle();
  assert.equal(log.at(-1), 'play:dos');
  finishPlayback(); for (let i = 0; i < 4; i++) await settle();
  finishPlayback();
  assert.equal(await queue.finished(), 'ended');
});

test('una pausa detiene la cola y conserva lo que faltaba; un fallo no se oculta', async () => {
  const paused = new SpeechQueue({ synthesize: async text => text, play: async () => 'paused' });
  paused.push('uno'); paused.push('dos'); paused.push('tres');
  assert.equal(await paused.finished(), 'paused');
  assert.deepEqual(paused.remaining(), ['dos', 'tres']);
  const failing = new SpeechQueue({ synthesize: async () => { throw new Error('voice_busy'); }, play: async () => 'ended' });
  failing.push('uno'); failing.push('dos');
  await assert.rejects(failing.finished(), /voice_busy/);
  const controller = new AbortController();
  const aborted = new SpeechQueue({ signal: controller.signal,
    synthesize: async () => { controller.abort(); throw new Error('AbortError'); }, play: async () => 'ended' });
  aborted.push('uno');
  assert.equal(await aborted.finished(), 'idle', 'cancelar no se informa como error');
});
