import test from 'node:test';
import assert from 'node:assert/strict';
import { redact, protectSecret } from './private-api.mjs';
test('los fallos de prueba ocultan encabezados y valores usados en formularios', () => {
  const fixture = 'fixture-credential-never-a-real-key'; protectSecret(fixture);
  const output = redact(`Authorization: Bearer header-test-value\nfill("${fixture}")\nthn_${'x'.repeat(64)}`);
  assert(!output.includes(fixture)); assert(!output.includes('header-test-value'));
  assert(!output.includes('x'.repeat(64))); assert(output.includes('[REDACTED]'));
});
