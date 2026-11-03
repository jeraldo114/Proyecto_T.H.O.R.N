import assert from 'node:assert/strict';
import test from 'node:test';
import { GestureInterpreter } from '../src/gestures.js';
function palm() {
  const p = Array.from({ length: 21 }, () => ({ x: .5, y: .6 }));
  for (const i of [8, 12, 16, 20]) p[i].y = .1;
  p[4] = { x: .1, y: .8 }; p[5].x = .2; p[17].x = .8;
  return p;
}
test('una detección perdida corta la pinza y nunca permite armar con muestras separadas', () => {
  const g = new GestureInterpreter(), p = palm();
  g.update(p, 0); g.update([], 300); assert.equal(g.update(p, 650).state, 'hold_palm');
  g.update(p, 1300); g.update(p, 1950); // intervalos de 650 ms no cuentan como palma sostenida
  assert.equal(g.armed, false);
  g.update(p, 2050); g.update(p, 2250); g.update(p, 2450); g.update(p, 2650);
  assert(g.armed); p[4] = { ...p[8] };
  g.update(p, 2750); assert(g.update(p, 2850).dragging);
  const lost = g.update([], 2900); assert(lost.cancelled); assert.equal(lost.dragging, false);
});
test('el cursor filtra temblores pequeños y suaviza desplazamientos', () => {
  const g = new GestureInterpreter(), p = palm();
  const a = g.update(p, 0); p[8].x += .002;
  assert.equal(g.update(p, 125).x, a.x);
  p[8].x = .7; const moved = g.update(p, 250);
  assert(moved.x < .5 && moved.x > .3);
  assert.equal(g.update(p, 200).state, 'waiting');
});
