import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8004';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const token = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(token); await waitForServer(url);
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const browser = await chromium.launch({ executablePath: existsSync(chrome) ? chrome : undefined,
  headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const artifacts = resolve(root, '.artifacts'); await mkdir(artifacts, { recursive: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage(), errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!request.url().startsWith(url) && !request.url().startsWith('blob:')) external.push(request.url()); });
  const modules = await context.request.get(`${url}/api/v1/capabilities/`, { headers: { Authorization: `Bearer ${token}` } }).then(result => result.json());
  await context.route('**/api/v1/capabilities/', route => route.fulfill({ json: { ...modules,
    microphone: { ...modules.microphone, available: false }, conversation: { auto_start: false },
    voice: { ...modules.voice, available: false, can_request: false, state: 'disabled' } } }));
  await context.route('**/api/v1/voice/speech/', () => assert.fail('No debe generar voz de pago.'));
  let requests = 0, mode = 'ok';
  const sources = [{ title: 'Fuente de prueba', url: 'https://example.com/reference',
    excerpt: '<img src=x onerror="window.testInjection=true"> Datos de una fuente externa.', retrieved_at: '2026-10-14T12:00:00Z' }];
  const result = { sources, reply: 'Resumen de prueba con referencia [1].', source: 'internet',
    provider: 'wikipedia', summary_method: 'local_llm', suggested_action: null, executed: false };
  await context.route('**/api/v1/web/**', async route => {
    requests++; const current = mode, request = route.request();
    assert.equal(request.postDataJSON().internet_consent, true);
    if (current === 'slow') await new Promise(resolve => setTimeout(resolve, 800));
    if (current === 'error') await route.fulfill({ status: 400, json: { error: 'web_public_https_required' } });
    else await route.fulfill({ json: { ...result, reply: current === 'slow' ? 'RESPUESTA TARDIA' : result.reply } }).catch(() => {});
  });
  await context.route('**/api/v1/network/diagnose/', route => { requests++; return route.fulfill({ json: {
    target: 'https://example.com/', dns_ms: 12.34, https_ms: 56.78, checked_at: '2026-10-14T12:00:00Z', reachable: true } }); });
  await page.goto(url);
  await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.locator('[data-view=network]').click();
  assert(await page.locator('#internet-consent').isDisabled());
  await page.locator('#session-button').click(); await page.locator('#token-input').fill(token);
  await page.locator('#session-form button[type=submit]').click();
  await page.waitForFunction(() => document.querySelector('#network-fresh').textContent === 'MUESTRA VIGENTE');
  await page.waitForFunction(() => document.querySelector('#network-upload').textContent !== '—');
  const native = await context.request.get(`${url}/api/v1/network/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
  assert(native.snapshot.interfaces.length > 0); assert(native.sensor.fresh);
  assert.equal(requests, 0, 'El polling local no consulta Internet.');
  assert(await page.locator('#web-submit').isDisabled());
  await page.locator('#internet-consent').check();
  await page.locator('#web-input').fill('seguridad'); await page.locator('#web-submit').click();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.startsWith('Resumen de prueba'));
  assert.equal(requests, 1);
  assert.equal(await page.locator('#console-sources a').getAttribute('href'), 'https://example.com/reference');
  assert.equal(await page.locator('#web-sources img').count(), 0);
  assert.equal(await page.evaluate(() => window.testInjection), undefined);
  assert(await page.locator('#web-sources').textContent().then(text => text.includes('<img')));
  await page.locator('#close-console').click();
  await page.screenshot({ path: resolve(artifacts, 'network-desktop.png'), fullPage: true });
  await page.locator('#network-diagnose').click();
  await page.waitForFunction(() => document.querySelector('#network-probe-result').textContent.includes('12.34'));
  await page.locator('#close-console').click();
  await page.locator('input[name=web-mode][value=read]').check();
  mode = 'error'; await page.locator('#web-input').fill('https://127.0.0.1'); await page.locator('#web-submit').click();
  await page.waitForFunction(() => document.querySelector('#web-result').textContent.includes('HTTPS públicas'));
  await page.locator('#close-console').click();
  mode = 'slow'; await page.locator('#web-input').fill('https://example.com/'); await page.locator('#web-submit').click();
  await page.waitForFunction(() => !document.querySelector('#web-cancel').disabled);
  await page.locator('#internet-consent').uncheck(); await page.locator('#internet-consent').check();
  await page.waitForTimeout(1100);
  assert(!await page.locator('#console-text').textContent().then(text => text.includes('RESPUESTA TARDIA')));
  assert.equal(await page.locator('#console-sources a').count(), 0);
  await page.locator('#close-console').click();
  mode = 'ok';
  await page.locator('#command-input').fill('busca en internet cifrado'); await page.locator('#command-form').evaluate(form => form.requestSubmit());
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.startsWith('Resumen de prueba'));
  await page.locator('#close-console').click();
  for (const width of [320, 390, 768, 1440, 1920]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 960 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Sin overflow en ${width}.`);
    if (width === 390) await page.screenshot({ path: resolve(artifacts, 'network-mobile.png'), fullPage: true });
  }
  await page.locator('#session-button').click(); await page.locator('#disconnect-button').click();
  assert(!await page.locator('#internet-consent').isChecked());
  assert.equal(await page.locator('#network-interfaces').textContent(), '');
  assert.equal(await page.locator('#web-sources a').count(), 0);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal((await context.cookies()).length, 0); assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log('Red E2E correcta: contadores locales reales, consentimiento, busqueda/lectura simuladas, fuentes sin HTML ejecutable, cancelacion tardia, consola y responsive; sin llamadas externas ni cargos.');
} finally { await browser.close(); }
