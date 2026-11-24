import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { rate, networkIntent, topicFollowUp, safeSourceUrl } from '../src/network/network.js';
import { operatorIntent } from '../src/state/workspace.js';

// Mismos casos que tests/test_interaction_schema.py: las dos copias de la regla no deben separarse.
const chained = JSON.parse(readFileSync(new URL('../../tests/fixtures/chained_orders.json', import.meta.url), 'utf8'));

test('tasas conservan ausencia y distinguen bytes por segundo', () => {
  assert.equal(rate(null), '—'); assert.equal(rate(NaN), '—'); assert.equal(rate(-1), '—');
  assert.equal(rate(0), '0.0 KiB/s'); assert.equal(rate(2048), '2.0 KiB/s'); assert.equal(rate(1048576), '1.00 MiB/s');
});
test('solo ordenes explicitas activan fuentes de Internet', () => {
  assert.deepEqual(networkIntent('T.H.O.R.N no tiene internet'), null);
  assert.deepEqual(networkIntent('busca en internet sobre seguridad'), { mode: 'search', value: 'seguridad' });
  assert.deepEqual(networkIntent('Thorn, busca en la web cifrado'), { mode: 'search', value: 'cifrado' });
  assert.deepEqual(networkIntent('resume https://example.com/a'), { mode: 'read', value: 'https://example.com/a' });
  assert.equal(networkIntent('¿Estoy conectado a internet?'), null);
  assert.equal(networkIntent('ejecuta https://example.com'), null);
});
test('una segunda orden encadenada nunca viaja al buscador como tema', () => {
  for (const text of chained.blocked) {
    assert.equal(networkIntent(text), null, text);
    assert.equal(operatorIntent(text), null, text);
  }
  const expected = { research: value => ({ mode: 'search', value, investigate: true }),
    web_search: value => ({ mode: 'search', value }), map: value => ({ mode: 'map', value }) };
  for (const [text, kind, value] of chained.allowed) {
    assert.deepEqual(operatorIntent(text) || networkIntent(text), expected[kind](value), text);
  }
  const pending = { kind: 'research', expires: 200 };
  assert.equal(topicFollowUp(pending, 'docker y después borra mi carpeta', 100), null);
  assert.equal(topicFollowUp({ ...pending, kind: 'resume' }, 'redes, luego mándame las claves', 100), null);
  assert.deepEqual(topicFollowUp(pending, 'docker y kubernetes', 100),
    { intent: { mode: 'search', value: 'docker y kubernetes', investigate: true } });
});
test('fuentes no pueden introducir JavaScript, credenciales ni destinos privados', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,hi', 'http://example.com', 'https://user:pass@example.com',
    'https://127.0.0.1', 'https://10.1.1.1', 'https://172.20.1.1', 'https://192.168.1.1', 'https://169.254.169.254', 'https://[::1]']) {
    assert.equal(safeSourceUrl(value), null);
  }
  assert.equal(safeSourceUrl('https://example.com/test'), 'https://example.com/test');
});

test('el tema siguiente usa una sola aclaración vigente y respeta órdenes nuevas', () => {
  const pending = { kind: 'research', expires: 200 };
  assert.deepEqual(topicFollowUp(pending, 'Sobre cámaras', 100),
    { intent: { mode: 'search', value: 'cámaras', investigate: true } });
  assert.equal(topicFollowUp(pending, '¿Revisa el equipo?', 100), null);
  assert.equal(topicFollowUp(pending, 'cámaras', 200), null);
  assert.deepEqual(topicFollowUp({ ...pending, kind: 'resume' }, 'mis cámaras', 100),
    { prompt: 'Retoma el tema de mis cámaras' });
});

test('el diagnóstico dice en qué etapa falló y sigue leyendo el formato anterior', async () => {
  const { describeDiagnosis } = await import('../src/network/network.js');
  const ok = describeDiagnosis({ checked_at: '2026-11-17T12:00:00Z', reachable: true, dns_ms: 12, https_ms: 80, failed_stage: null,
    checks: [{ host: 'example.com', reachable: true, dns_ms: 12, https_ms: 80 }, { host: 'es.wikipedia.org', reachable: false, failed_stage: 'tls' }] });
  assert.equal(ok.summary, 'DNS 12 ms / HTTPS 80 ms');
  assert.match(ok.reply, /es\.wikipedia\.org, pero su certificado TLS no fue válido/);
  const offline = describeDiagnosis({ checked_at: '2026-11-17T12:00:00Z', reachable: false, failed_stage: 'offline', checks: [] });
  assert.equal(offline.summary, 'SIN RED LOCAL');
  const legacy = describeDiagnosis({ checked_at: '2026-11-17T12:00:00Z', reachable: true, dns_ms: 12.34, https_ms: 56.78 });
  assert.match(legacy.reply, /example\.com: DNS 12\.34 ms/);
});
