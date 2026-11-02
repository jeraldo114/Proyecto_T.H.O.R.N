import test from 'node:test';
import assert from 'node:assert/strict';
import { percent, gib, duration, eventMatches, appendSample } from '../src/model.js';

test('no convierte datos ausentes en porcentajes fabricados', () => {
  for (const value of [undefined, null, '25', NaN, Infinity, -1, 101]) assert.equal(percent(value), null);
  assert.equal(percent(0), 0); assert.equal(percent(90.5), 90.5);
});
test('formatos usan valores reales y unidades explicitas', () => {
  assert.equal(gib(1024 ** 3), '1.0 GiB'); assert.equal(gib(null), '—');
  assert.equal(duration(3661.9), '01:01:01'); assert.equal(duration(-1), '—');
});
test('el historial solo crece por muestras nuevas, no por cada consulta', () => {
  const sample = { sampled_at: '2026-10-06T12:00:00Z', cpu_percent: 20 };
  const history = appendSample([], sample);
  assert.equal(history.length, 1); assert.equal(appendSample(history, sample), history);
  assert.equal(appendSample(history, { sampled_at: 'invalid' }), history);
  const result = appendSample(history, { ...sample, sampled_at: '2026-10-06T12:00:02Z' }, 1);
  assert.equal(result.length, 1); assert.equal(result[0].sampled_at, '2026-10-06T12:00:02Z');
});
test('filtros clasifican eventos observados', () => {
  assert.equal(eventMatches({ event_type: 'module.error' }, 'error'), true);
  assert.equal(eventMatches({ event_type: 'system.snapshot' }, 'sensor'), true);
  assert.equal(eventMatches({ event_type: 'system.snapshot' }, 'command'), false);
});
