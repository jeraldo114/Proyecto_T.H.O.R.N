import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8004';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const token = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(token); await waitForServer(url);
const executablePath = process.env.THORN_BROWSER || (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
const live = process.env.THORN_MIC_LIVE === '1';
const artifacts = resolve(root, '.artifacts');
await mkdir(artifacts, { recursive: true });
let samples;
// Una muestra ya generada se decodifica LOCALMENTE. No llama a ElevenLabs.
if (process.env.THORN_MIC_FIXTURE_MP3) {
  const decoder = await chromium.launch({ executablePath, headless: true });
  try {
    const tab = await decoder.newPage();
    const audio = (await readFile(process.env.THORN_MIC_FIXTURE_MP3)).toString('base64');
    samples = await tab.evaluate(async encoded => {
      const context = new AudioContext({ sampleRate: 16000 });
      try {
        const raw = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
        const decoded = await context.decodeAudioData(raw.buffer);
        return Array.from(decoded.getChannelData(0));
      } finally { await context.close(); }
    }, audio);
  } finally { await decoder.close(); }
} else samples = Array.from({ length: 16000 * 3 }, (_, i) => 0.2 * Math.sin(i * 2 * Math.PI * 220 / 16000));
const fixture = resolve(artifacts, 'microphone-fixture.wav');
const wav = Buffer.alloc(44 + samples.length * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
wav.write('data', 36); wav.writeUInt32LE(samples.length * 2, 40);
samples.forEach((value, i) => wav.writeInt16LE(Math.round(Math.max(-1, Math.min(1, value)) * 32767), 44 + i * 2));
await writeFile(fixture, wav);
const browser = await chromium.launch({ executablePath, headless: true,
  args: ['--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${fixture}`, '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.grantPermissions(['microphone'], { origin: url });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let uploads = 0, speech = 0, chats = 0;
  await context.route('**/api/v1/voice/speech/', async route => { speech++; await route.abort(); });
  page.on('request', request => { if (request.url().endsWith('/chat/')) chats++; });
  const capabilities = await context.request.get(`${url}/api/v1/capabilities/`, { headers: { Authorization: `Bearer ${token}` } }).then(result => result.json());
  if (live) assert.equal(capabilities.microphone.state, 'ready', 'El motor Vosk real debe estar instalado.');
  await context.route('**/api/v1/capabilities/', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...capabilities,
    conversation: { ...capabilities.conversation, auto_start: false },
    voice: { ...capabilities.voice, available: false, can_request: false, state: 'disabled' },
    microphone: { ...capabilities.microphone, state: 'ready', available: true, busy: false } }) }));
  await context.route('**/api/v1/microphone/transcribe/', async route => {
    uploads++;
    const request = route.request();
    assert.equal(request.headers()['x-thorn-microphone-consent'], 'local');
    assert.equal(request.headers()['content-type'], 'application/octet-stream');
    assert(request.postDataBuffer().length >= 3200 && request.postDataBuffer().length <= 480000);
    assert.equal(request.postDataBuffer().length % 2, 0);
    if (live) await route.continue();
    else await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ text: 'prueba de dictado', cloud: false, has_speech: true }) });
  });
  await page.goto(url);
  await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.locator('#session-button').click(); await page.locator('#token-input').fill(token);
  await page.locator('#session-form button[type=submit]').click();
  await page.locator('[data-view=assistant]').click();
  await page.waitForFunction(() => !document.querySelector('#microphone-start').disabled);
  await page.evaluate(() => {
    window.testMicStreams = [];
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async options => {
      const stream = await original(options); window.testMicStreams.push(stream); return stream;
    };
    window.testMicGum = navigator.mediaDevices.getUserMedia;
  });
  const released = () => page.waitForFunction(() => window.testMicStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
  assert.equal(uploads, 0);
  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Escuchando'));
  await page.waitForFunction(() => document.querySelector('#microphone-level').value > 0);
  await page.locator('#microphone-cancel').click(); await released();
  assert.equal(uploads, 0, 'Cancelar no envia audio.');

  // Permiso tardio: el stream que llega despues de cancelar tambien debe cerrarse.
  await page.evaluate(() => { navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { window.testMicGrant = resolve; }); });
  await page.locator('#microphone-start').click(); await page.locator('#microphone-cancel').click();
  await page.evaluate(async () => { const stream = await window.testMicGum({ audio: true }); window.testMicGrant(stream); navigator.mediaDevices.getUserMedia = window.testMicGum; });
  await released(); assert.equal(uploads, 0);
  await page.evaluate(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('test', 'NotAllowedError')); });
  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Permiso denegado'));
  await page.evaluate(() => { navigator.mediaDevices.getUserMedia = window.testMicGum; });

  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Escuchando'));
  await page.waitForTimeout(live ? 9000 : 1200);
  await page.locator('#microphone-finish').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent === 'Dictado listo', null, { timeout: 45000 });
  await released(); assert.equal(uploads, 1);
  const transcript = await page.locator('#microphone-text').inputValue();
  assert(transcript.length > 3);
  assert.equal(chats, 0, 'Una transcripcion no envia consultas automaticamente.');
  assert.equal(speech, 0, 'Dictar no sintetiza ni envia texto a ElevenLabs.');
  await page.locator('#microphone-text').fill('estado'); await page.locator('#microphone-send').click();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('Núcleo: running'));
  await page.locator('#close-console').click();

  // El tope automatico debe cerrar el dispositivo y enviar como maximo 15 s.
  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent === 'Dictado listo', null, { timeout: 45000 });
  await released(); assert.equal(uploads, 2);

  // Cancelar una respuesta tardia no debe restaurar su texto en una sesion nueva.
  await context.unroute('**/api/v1/microphone/transcribe/');
  await context.route('**/api/v1/microphone/transcribe/', async route => {
    uploads++;
    await new Promise(resolve => setTimeout(resolve, 700));
    await route.fulfill({ contentType: 'application/json', body: '{"text":"texto tardio que no debe aparecer","cloud":false}' }).catch(() => {});
  });
  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Escuchando'));
  await page.waitForTimeout(400); await page.locator('#microphone-finish').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Micrófono cerrado'));
  await page.waitForTimeout(150); await page.locator('#microphone-cancel').click(); await released();
  await page.waitForTimeout(1000);
  assert.equal(await page.locator('#microphone-text').inputValue(), '');
  assert.equal(await page.locator('#microphone-result').innerText(), 'Dictado cancelado');

  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Desborde de microfono a ${width}px`);
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: resolve(artifacts, 'hud-microphone-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifacts, 'hud-microphone-mobile.png'), fullPage: true });
  await page.locator('#microphone-start').click();
  await page.waitForFunction(() => document.querySelector('#microphone-result').textContent.startsWith('Escuchando'));
  await page.locator('#session-button').click(); await page.locator('#disconnect-button').click(); await released();
  assert.equal(uploads, 3); assert.equal(await page.locator('#microphone-text').inputValue(), '');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.deepEqual(errors, []);
  console.log(`Microfono E2E correcto: permiso, cancelacion tardia, PCM, privacidad, consulta manual y responsive. Reconocimiento ${live ? 'Vosk real con fuente de audio simulada' : 'simulado'}.`);
} finally { await browser.close(); }
