import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { installPrivateDiagnostics, protectSecret, waitForServer } from './private-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8005';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const ownServer = process.env.THORN_TEST_START_SERVER === '1';
const authority = ownServer ? randomBytes(40).toString('hex') : process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c',
  'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(authority);
let server, browser, context, session;
try {
  if (ownServer) server = spawn(python, ['-B', 'serve.py', '--sin-db', '--port', new URL(url).port], {
    cwd: root, stdio: 'ignore', env: { ...process.env, THORN_API_TOKEN: authority,
      DJANGO_SECRET_KEY: randomBytes(40).toString('hex'), THORN_SECURE_STORAGE: '0', THORN_VOICE_ENABLED: '0',
      THORN_MICROPHONE_ENABLED: '0', THORN_BRAIN_AUTOSTART: '0', THORN_CONVERSATION_AUTO_START: '0' },
  });
  await waitForServer(url);
  const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  browser = await chromium.launch({ headless: true, executablePath: process.env.THORN_BROWSER || (existsSync(chrome) ? chrome : undefined),
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.route('**/api/v1/capabilities/', async route => {
    const response = await route.fetch(), value = await response.json();
    await route.fulfill({ response, json: { ...value, conversation: { ...value.conversation, auto_start: false } } });
  });
  const page = await context.newPage(), external = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/^https?:/.test(request.url()) && !request.url().startsWith(url + '/')) external.push(request.url()); });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.locator('#session-button').click(); await page.locator('#token-input').fill(authority);
  const exchange = page.waitForResponse(response => response.url().endsWith('/auth/session/') && response.status() === 200);
  await page.locator('#session-form button[type=submit]').click();
  session = protectSecret((await (await exchange).json()).access_token);
  await page.waitForFunction(() => document.querySelector('#session-label').textContent === 'Conectado');
  const rgb = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 700; canvas.height = 230;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 700, 230);
    ctx.fillStyle = 'black'; ctx.font = '32px Arial'; ctx.fillText('THORN MEMORIA LOCAL', 24, 65);
    ctx.fillText('El fusible F9 protege el equipo.', 24, 125);
    const pixels = ctx.getImageData(0, 0, 700, 230).data;
    const bytes = new Uint8Array(700 * 230 * 3);
    for (let i = 0, j = 0; i < pixels.length; i += 4) { bytes[j++] = pixels[i]; bytes[j++] = pixels[i+1]; bytes[j++] = pixels[i+2]; }
    let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte); return btoa(binary);
  });
  const image = deflateSync(Buffer.from(rgb, 'base64'));
  const paint = 'q 700 0 0 230 0 0 cm /Im1 Do Q';
  const objects = [
    Buffer.from('<< /Type /Catalog /Pages 2 0 R >>'),
    Buffer.from('<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
    Buffer.from('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 700 230] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>'),
    Buffer.concat([Buffer.from(`<< /Type /XObject /Subtype /Image /Width 700 /Height 230 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length ${image.length} >>\nstream\n`), image, Buffer.from('\nendstream')]),
    Buffer.from(`<< /Length ${paint.length} >>\nstream\n${paint}\nendstream`),
  ];
  const chunks = [Buffer.from('%PDF-1.4\n')], offsets = [0]; let length = chunks[0].length;
  objects.forEach((object, i) => { offsets.push(length); const value = Buffer.concat([Buffer.from(`${i+1} 0 obj\n`), object, Buffer.from('\nendobj\n')]); chunks.push(value); length += value.length; });
  const xref = `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n => `${String(n).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${length}\n%%EOF\n`;
  const pdf = Buffer.concat([...chunks, Buffer.from(xref)]);
  await page.locator('[data-tool=documents]').click();
  await page.locator('#b9-file').setInputFiles({ name: 'ocr-prueba.pdf', mimeType: 'application/pdf', buffer: pdf });
  await page.locator('#b9-document-form button').click();
  const panel = page.locator('.panel-document').last(); await panel.waitFor({ state: 'visible' });
  await panel.locator('canvas[data-rendered=true]').waitFor();
  const attached = page.waitForResponse(response => response.url().endsWith('/documents/ocr/') && response.status() === 200, { timeout: 60000 });
  await panel.getByRole('button', { name: 'Reconocer texto de esta página con OCR local' }).click();
  const result = await (await attached).json();
  assert(result.document.ocr); assert.match(result.document.pages[0].text, /fusible F9/i);
  assert.equal(result.source, 'local_browser_ocr');
  await panel.getByRole('button', { name: 'Texto', exact: true }).click();
  assert.match(await panel.locator('.document-extracted').innerText(), /F9/);
  assert.equal(external.length, 0, 'El OCR no debe recurrir a CDN ni servicios externos.');
  assert.deepEqual(errors, []);
  console.log('OCR E2E correcto: PDF escaneado real → canvas → WASM local español → texto adjunto al documento; sin solicitudes externas.');
} finally {
  if (session && context) await context.request.post(`${url}/api/v1/auth/revoke/`, { headers: { Authorization: `Bearer ${session}` }, data: {} }).catch(() => {});
  await browser?.close();
  if (server) { server.kill('SIGINT'); await new Promise(done => { server.once('exit', done); setTimeout(() => { server.kill('SIGKILL'); done(); }, 5000).unref(); }); }
}
