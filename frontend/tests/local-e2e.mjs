// Integracion REAL: perfil Ollama + Piper, sin solicitudes a APIs de pago.
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
const python = process.env.THORN_PYTHON || resolve(root, '.venv/Scripts/python.exe');
const authority = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c',
  'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(authority); await waitForServer(url);
const browser = await chromium.launch({ headless: true,
  executablePath: process.env.THORN_BROWSER || (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
let session;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const response = await context.request.post(`${url}/api/v1/auth/session/`, { headers: { Authorization: `Bearer ${authority}` }, data: {} });
  assert.equal(response.status(), 200); session = await response.json();
  const errors = [], external = [], tokens = new Set(); let voices = 0, renewals = 0, maxLevel = 0;
  await context.route('**/*', async route => {
    const address = route.request().url();
    if (!address.startsWith(url + '/') && !address.startsWith('blob:')) { external.push(address); await route.abort(); }
    else await route.continue();
  });
  await context.route('**/api/v1/capabilities/', async route => {
    const result = await route.fetch(); const data = await result.json();
    assert.equal(data.identity.local_ai_only, true); assert.equal(data.identity.self_trained, false);
    assert.equal(data.language.model, 'thorn:bastion9'); assert.equal(data.language.available, true);
    assert.equal(data.voice.provider, 'piper'); assert.equal(data.voice.cloud, false); assert.equal(data.voice.state, 'ready');
    await route.fulfill({ response: result, json: { ...data, conversation: { ...data.conversation, auto_start: false } } });
  });
  // Este caso mide dos sintesis concretas. El saludo de Bastion 9 se prueba
  // aparte, sin modificar las preferencias persistentes del operador.
  await context.route('**/api/v1/preferences/', async route => {
    const response = await route.fetch(), data = await response.json();
    await route.fulfill({ response, json: { ...data, preferences: { ...data.preferences, briefing_enabled: false } } });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const value = request.headers().authorization;
    if (value) { assert.notEqual(value, `Bearer ${authority}`); tokens.add(value.slice(7)); }
    if (request.url().endsWith('/auth/renew/')) renewals++;
    if (request.url().endsWith('/voice/speech/')) { voices++; assert.equal(request.postDataJSON().cloud_consent, false); }
  });
  await page.addInitScript(() => {
    window.localWaves = [];
    const originalFetch = fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (args[0] === '/api/v1/voice/speech/' && response.ok) {
        // CDP puede omitir cuerpos clasificados como media; observar el mismo
        // buffer que recibe el reproductor verifica la respuesta real.
        const bytes = await response.clone().arrayBuffer();
        window.localWaves.push({ length: bytes.byteLength, magic: new TextDecoder().decode(bytes.slice(0, 4)) });
      }
      return response;
    };
    setInterval(() => {
    const value = Number(document.querySelector('#core-canvas')?.dataset.voiceLevel || 0);
    window.maxLocalVoiceLevel = Math.max(window.maxLocalVoiceLevel || 0, value);
  }, 20); });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  // Es el mismo payload efimero que entrega el escritorio, nunca la autoridad.
  await page.evaluate(value => window.dispatchEvent(new CustomEvent('thorn-native-session', { detail: value })), session);
  await page.waitForFunction(() => document.querySelector('#sample-state').textContent === 'MUESTRA VIGENTE');
  await page.locator('[data-view=assistant]').click();
  assert(await page.locator('#voice-auto').isChecked()); assert(!await page.locator('#voice-consent').isVisible());
  assert.match(await page.locator('#ai-policy').innerText(), /SIN CRÉDITOS/);
  await page.locator('#voice-text').fill('Soy T.H.O.R.N. Esta voz se genera en tu equipo. Listo para continuar.');
  const waveResponse = page.waitForResponse(response => response.url().endsWith('/voice/speech/'));
  await page.locator('#voice-test-button').click();
  const wave = await waveResponse;
  assert.equal(wave.status(), 200); assert.match(wave.headers()['content-type'], /audio\/wav/);
  await page.waitForFunction(() => !document.querySelector('#voice-result').textContent.startsWith('Sintetizando'));
  const audio = await page.evaluate(() => window.localWaves[0]);
  assert(audio.length > 1000); assert.equal(audio.magic, 'RIFF');
  if (await page.locator('#voice-resume-button').isVisible()) await page.locator('#voice-resume-button').click();
  await page.waitForFunction(() => window.maxLocalVoiceLevel > 0.005, { timeout: 10000 });
  maxLevel = await page.evaluate(() => window.maxLocalVoiceLevel);
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Reproducción terminada');
  const artifacts = resolve(root, '.artifacts'); await mkdir(artifacts, { recursive: true });
  await page.screenshot({ path: resolve(artifacts, 'local-assistant.png'), fullPage: true });

  // Simula el paso de 13 minutos; el servidor mantiene su reloj monotono real.
  await page.evaluate(() => { const original = Date.now.bind(Date); Date.now = () => original() + 800000; });
  await page.locator('#refresh-button').evaluate(button => button.click());
  await page.waitForFunction(() => document.querySelector('#sample-state').textContent === 'MUESTRA VIGENTE');
  await page.waitForResponse(response => response.url().endsWith('/api/v1/system/'));
  assert.equal(renewals, 1); assert.equal(tokens.size, 2);

  await page.locator('#command-input').fill('Identificate como T.H.O.R.N en una frase breve.');
  const chatResponse = page.waitForResponse(response => response.url().endsWith('/chat/'), { timeout: 80000 });
  const spokenResponse = page.waitForResponse(response => response.url().endsWith('/voice/speech/'), { timeout: 85000 });
  const start = performance.now(); await page.locator('#command-form').evaluate(form => form.requestSubmit());
  const healthStart = performance.now(); assert((await context.request.get(`${url}/api/v1/health/`)).ok());
  const healthMs = Math.round(performance.now() - healthStart);
  const chat = await chatResponse; assert.equal(chat.status(), 200);
  const reply = await chat.json(); assert(reply.reply.trim()); assert.equal(reply.executed, false);
  assert.equal((await spokenResponse).status(), 200); assert.equal(voices, 2);
  const chatMs = Math.round(performance.now() - start);
  if (await page.locator('#stop-reply-button').isVisible()) await page.locator('#stop-reply-button').click();
  else await page.locator('.panel-answer').last().getByRole('button', { name: 'Detener voz', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifacts, 'local-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal((await context.cookies()).length, 0); assert(!await page.content().then(html => html.includes(authority)));
  await page.locator('#session-button').click(); await page.locator('#disconnect-button').click();
  for (const token of tokens) {
    const result = await context.request.get(`${url}/api/v1/activity/`, { headers: { Authorization: `Bearer ${token}` } });
    assert.equal(result.status(), 401);
  }
  assert.deepEqual(external, []); assert.deepEqual(errors, []);
  console.log(JSON.stringify({ local: true, wavBytes: audio.length, maxVoiceLevel: maxLevel, renewals, chatMs, healthMs, screenshots: ['local-assistant.png', 'local-mobile.png'] }));
} finally {
  if (session) await fetch(`${url}/api/v1/auth/revoke/`, { method: 'POST', headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
  await browser.close();
}
