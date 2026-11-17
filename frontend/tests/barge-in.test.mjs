import assert from 'node:assert/strict';
import test from 'node:test';
import { classifyInterruption, looksLikeEcho } from '../src/barge-in.js';
import { stripWakeWord, WakeWordGate } from '../src/wake-word.js';

test('clasifica pausa, asentimientos, eco y una orden nueva', () => {
  const spoken = 'La CPU está al 12 % y la memoria RAM al 40 %. Todo parece estable.';
  for (const text of ['Thorn, espera', 'tom espera', 'espera un momento', 'para', 'Silencio, por favor', 'oye thorn para'])
    assert.equal(classifyInterruption(text, spoken), 'pause', text);
  for (const text of ['', '  ', 'sí', 'ajá', 'mmm', 'ok'])
    assert.equal(classifyInterruption(text, spoken), 'noise', text);
  for (const text of ['la memoria ram al cuarenta', 'todo parece estable'])
    assert.equal(classifyInterruption(text, spoken), 'echo', text);
  for (const text of ['para qué sirve la red', 'mejor dime la hora', 'no, abre el sistema'])
    assert.equal(classifyInterruption(text, spoken), 'replace', text);
});

test('el eco exige que la mayoría de lo oído ya estuviera en la respuesta', () => {
  assert.equal(looksLikeEcho('estable', 'Todo parece estable'), true);
  assert.equal(looksLikeEcho('abre el navegador estable', 'Todo parece estable'), false);
  assert.equal(looksLikeEcho('algo', ''), false);
});

test('el nombre incluye los errores medidos del STT sin tocar palabras completas', () => {
  for (const text of ['Fourth, abre el sistema', 'Thorne abre el sistema', 'Tom, abre el sistema', 'Oye ton abre el sistema'])
    assert.deepEqual(stripWakeWord(text), { matched: true, text: 'abre el sistema' }, text);
  for (const text of ['tomate fresco', 'tonto el que lo lea', 'torneo de ajedrez'])
    assert.equal(stripWakeWord(text).matched, false, text);
  const gate = new WakeWordGate({ clock: () => 0 });
  assert.deepEqual(gate.activate('Thorn abre la red'), { accepted: true, activated: true, text: 'abre la red' });
  assert.equal(gate.open, true);
});

test('los números de la respuesta se comparan como los escribe el STT', async () => {
  const { numberWords } = await import('../src/barge-in.js');
  assert.deepEqual([0, 16, 21, 40, 45, 100, 115, 999, 1000, 2026].map(numberWords),
    ['cero', 'dieciseis', 'veintiuno', 'cuarenta', 'cuarenta y cinco', 'cien', 'ciento quince',
      'novecientos noventa y nueve', 'mil', 'dos mil veintiseis']);
  assert.equal(looksLikeEcho('la memoria al cuarenta por ciento', 'La memoria está al 40 %.'), true);
});
