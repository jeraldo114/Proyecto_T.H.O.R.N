const secrets = new Set();
export function protectSecret(value) { if (typeof value === 'string' && value) secrets.add(value); return value; }
export function redact(value) {
  let result = String(value);
  for (const secret of secrets) result = result.split(secret).join('[REDACTED]');
  return result.replace(/(authorization\s*[:=]\s*['"]?bearer)\s+[^\s'"]+/gi, '$1 [REDACTED]')
    .replace(/thn_[A-Za-z0-9_-]{32,}/g, '[REDACTED_SESSION]');
}
let installed = false;
export function installPrivateDiagnostics(secret) {
  protectSecret(secret);
  if (installed) return; installed = true;
  const fail = error => { process.stderr.write(redact(error?.stack || error?.message || error) + '\n'); process.exit(1); };
  process.once('uncaughtException', fail); process.once('unhandledRejection', fail);
}
export async function waitForServer(url) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    try { if ((await fetch(`${url}/api/v1/health/`, { signal: AbortSignal.timeout(1500) })).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('El servidor no termino de arrancar. No se envio ninguna credencial.');
}
