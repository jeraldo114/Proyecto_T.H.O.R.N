import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..'), url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8004';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const authority = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(authority); await waitForServer(url);
const systemChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const browser = await chromium.launch({ executablePath: process.env.THORN_BROWSER || (existsSync(systemChrome) ? systemChrome : undefined),
  headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const artifacts = resolve(root, '.artifacts/bastion9-fixtures');
const summary = {};
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('**/api/v1/capabilities/', async route => {
    const response = await route.fetch(), data = await response.json();
    await route.fulfill({ response, json: { ...data, conversation: { ...data.conversation, auto_start: false } } });
  });
  const issue = await context.request.post(`${url}/api/v1/auth/session/`, { headers: { Authorization: `Bearer ${authority}` }, data: {} });
  assert.equal(issue.status(), 200);
  const session = await issue.json();
  const headers = { Authorization: `Bearer ${session.access_token}` };
  const fixture = (await readFile(resolve(artifacts, 'hand.png'))).toString('base64');
  await context.addInitScript(encoded => {
    window.b9SyntheticCameraCalls = 0; window.b9SyntheticTracks = [];
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async options => {
      if (options.audio !== false) throw new Error('No se usa microfono en esta prueba');
      window.b9SyntheticCameraCalls++;
      const image = new Image(); image.src = `data:image/png;base64,${encoded}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const ctx = canvas.getContext('2d');
      const draw = () => { ctx.drawImage(image, 0, 0, 640, 480); requestAnimationFrame(draw); }; draw();
      const stream = canvas.captureStream(15); window.b9SyntheticTracks.push(...stream.getTracks());
      return stream;
    } });
  }, fixture);
  const page = await context.newPage();
  const errors = [], origins = new Set();
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().startsWith('http')) origins.add(new URL(request.url()).origin); });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.evaluate(value => window.dispatchEvent(new CustomEvent('thorn-native-session', { detail: value })), session);
  await page.waitForFunction(() => document.querySelector('#session-label').textContent === 'Conectado');
  await page.waitForFunction(() => document.querySelector('#b9-state').textContent === 'SESIÓN LOCAL');
  assert.equal(await page.evaluate(() => window.b9SyntheticCameraCalls), 0);
  await page.locator('[data-view=assistant]').click();
  await page.locator('#b9-file').setInputFiles(resolve(artifacts, 'prueba.pdf'));
  await page.locator('#b9-document-form button').click();
  await page.waitForFunction(() => document.querySelector('#b9-document-text').textContent.includes('CEDRO-9'));
  summary.pdf = 'Texto PDF extraido realmente, sin OCR ni almacenamiento permanente';

  const first = await context.request.post(`${url}/api/v1/chat/`, { headers, data: { prompt: 'Mi proyecto de prueba se llama Cedro. Confirma el nombre en una frase.' }, timeout: 90000 });
  assert.equal(first.status(), 200, await first.text());
  const follow = await context.request.post(`${url}/api/v1/chat/`, { headers, data: { prompt: 'Como se llama mi proyecto de prueba?' }, timeout: 90000 });
  assert.equal(follow.status(), 200, await follow.text());
  const answer = await follow.json(); assert.match(answer.reply, /cedro/i);
  summary.context = { messages: answer.context_messages, model: answer.model, follow_up_confirmed: true };

  await page.locator('[data-tool=camera]').click();
  await page.locator('#b9-camera-consent').check(); await page.locator('#b9-camera-start').click();
  await page.waitForFunction(() => document.querySelector('#b9-vision-state').textContent === 'VISTA LOCAL');
  await page.locator('#b9-gestures').check();
  await page.waitForFunction(() => document.querySelector('#b9-vision-state').textContent === 'SEGUIMIENTO LOCAL ACTIVADO', { timeout: 30000 });
  await page.waitForFunction(() => /Cursor local|Pinza activa|Mantén la palma/.test(document.querySelector('#b9-gesture-state').textContent), { timeout: 15000 });
  summary.vision = { state: await page.locator('#b9-gesture-state').innerText(), physical_camera_used: false, worker_model: true };
  await page.locator('#b9-camera-stop').click();
  await page.waitForFunction(() => window.b9SyntheticTracks.every(track => track.readyState === 'ended'));
  assert.equal(await page.locator('#b9-video').evaluate(v => v.srcObject), null);
  assert.equal(origins.size, 1); assert(origins.has(new URL(url).origin));
  assert.deepEqual(errors, []);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.screenshot({ path: resolve(artifacts, 'bastion9-tools.png') });
  await page.locator('[data-view=assistant]').click();
  await page.locator('#b9-clear').click();
  await page.waitForFunction(() => document.querySelector('#b9-operation').textContent.includes('borrados'));
  assert.equal(await page.locator('#b9-document-text').innerText(), '');
  assert.equal(await page.locator('#b9-doc-select option').count(), 1);
  const cleared = await context.request.get(`${url}/api/v1/workspace/`, { headers });
  const clearedState = await cleared.json();
  assert.equal(clearedState.context_messages, 0); assert.deepEqual(clearedState.panels, []);
  assert.equal(await page.locator('.panel-document').count(), 0);
  assert.deepEqual(errors, []);
  summary.reset = 'contexto, resultados y visor local vaciados';
  await context.request.post(`${url}/api/v1/auth/revoke/`, { headers, data: {} });
  assert.deepEqual(errors, []);
  summary.errors = errors; summary.browser_origins = [...origins];
  await writeFile(resolve(artifacts, 'e2e-summary.json'), JSON.stringify(summary, null, 2));
  console.log('Bastion 9 E2E correcto:', JSON.stringify(summary));
} finally { await browser.close(); }
