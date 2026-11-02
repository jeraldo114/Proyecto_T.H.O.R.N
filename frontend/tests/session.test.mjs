import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SessionClient } from '../src/session.js';
const payload = suffix => ({ access_token: `thn_${suffix.repeat(64)}`, expires_in: 900,
  session_id: suffix.repeat(32), scopes: ['telemetry:read'] });

test('renewal is single-flight and never reuses the authority', async () => {
  let now = 0; const calls = [];
  const session = new SessionClient({ clock: () => now, fetcher: async (url, options) => {
    calls.push({ url, options }); return { ok: true, json: async () => payload('b') };
  } });
  session.accept(payload('a'));
  assert.equal(await session.ensure(), payload('a').access_token);
  assert.equal(calls.length, 0); now = 800000;
  const values = await Promise.all([session.ensure(), session.ensure(), session.ensure()]);
  assert(values.every(value => value === payload('b').access_token)); assert.equal(calls.length, 1);
  assert.equal(calls[0].options.headers.Authorization, `Bearer ${payload('a').access_token}`);
  session.close(); assert.equal(calls.at(-1).url, '/api/v1/auth/revoke/');
  await assert.rejects(session.ensure(), { message: 'unauthorized' });
});

test('disconnect during renewal cannot resurrect a session', async () => {
  let now = 0, resolve; const calls = [];
  const session = new SessionClient({ clock: () => now, fetcher: (url, options) => {
    calls.push(options.headers.Authorization);
    return url.endsWith('/renew/') ? new Promise(done => { resolve = done; }) : Promise.resolve({ ok: true });
  } });
  session.accept(payload('a')); now = 800000;
  const pending = session.ensure(); session.close();
  resolve({ ok: true, json: async () => payload('b') });
  await assert.rejects(pending, { message: 'session_changed' });
  assert.equal(session.token, ''); assert(calls.includes(`Bearer ${payload('b').access_token}`));
});

test('persistent root and invalid expiration are never accepted as sessions', () => {
  const session = new SessionClient();
  assert.throws(() => session.accept({ ...payload('a'), access_token: 'permanent-root' }));
  assert.throws(() => session.accept({ ...payload('a'), expires_in: 86400 }));
  assert.equal(session.token, '');
});

test('accepting a rotated credential of the same family does not revoke it', () => {
  const calls = [];
  const session = new SessionClient({ fetcher: async url => { calls.push(url); return { ok: true }; } });
  session.accept(payload('a'));
  session.accept({ ...payload('b'), session_id: payload('a').session_id });
  assert.equal(calls.length, 0); assert.equal(session.token, payload('b').access_token);
  session.close(); assert.equal(calls.length, 1);
});
