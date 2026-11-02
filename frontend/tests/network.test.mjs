import test from 'node:test';
import assert from 'node:assert/strict';
import { rate, networkIntent, safeSourceUrl } from '../src/network.js';

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
test('fuentes no pueden introducir JavaScript, credenciales ni destinos privados', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,hi', 'http://example.com', 'https://user:pass@example.com',
    'https://127.0.0.1', 'https://10.1.1.1', 'https://172.20.1.1', 'https://192.168.1.1', 'https://169.254.169.254', 'https://[::1]']) {
    assert.equal(safeSourceUrl(value), null);
  }
  assert.equal(safeSourceUrl('https://example.com/test'), 'https://example.com/test');
});
