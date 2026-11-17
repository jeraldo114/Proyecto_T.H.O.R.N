import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Interrupción real: Silero en el navegador escucha un micrófono sintético
// mientras Thorn habla. Primero oye un eco de su propia respuesta (debe seguir
// escuchando: antes quedaba sorda aquí) y después una orden nueva que la reemplaza.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8004';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const token = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(token); await waitForServer(url);
const fixturePath = process.env.THORN_VOICE_FIXTURE_WAV;
assert(fixturePath, 'Requiere THORN_VOICE_FIXTURE_WAV: una frase hablada en WAV (por ejemplo, generada con Piper local).');
const clip = await readFile(fixturePath);
const executablePath = process.env.THORN_BROWSER || (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
const artifacts = resolve(root, '.artifacts'); await mkdir(artifacts, { recursive: true });

// El micrófono falso repite: 1 s de silencio, la frase y 2 s de silencio.
const decoder = await chromium.launch({ executablePath, headless: true });
let samples;
try {
  const tab = await decoder.newPage();
  samples = await tab.evaluate(async encoded => {
    const context = new AudioContext({ sampleRate: 16000 });
    try {
      const buffer = await context.decodeAudioData(Uint8Array.from(atob(encoded), char => char.charCodeAt(0)).buffer);
      return [...new Float32Array(16000), ...buffer.getChannelData(0).slice(0, 16000 * 5), ...new Float32Array(32000)];
    } finally { await context.close(); }
  }, clip.toString('base64'));
} finally { await decoder.close(); }
const wav = Buffer.alloc(44 + samples.length * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
wav.write('data', 36); wav.writeUInt32LE(samples.length * 2, 40);
samples.forEach((value, i) => wav.writeInt16LE(Math.round(Math.max(-1, Math.min(1, value)) * 32767), 44 + i * 2));
const fixture = resolve(artifacts, 'barge-in-fixture.wav'); await writeFile(fixture, wav);

const LONG = Array.from({ length: 10 }, (_, i) => `Parte ${i + 1} del informe largo sobre la red local.`).join(' ');
const browser = await chromium.launch({ executablePath, headless: true,
  args: ['--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${fixture}`, '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const heard = ['cuéntame algo largo', 'parte del informe largo sobre la red', 'mejor dime la hora'];
const prompts = [], spoken = [], errors = [], external = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.grantPermissions(['microphone'], { origin: url });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!request.url().startsWith(url) && !request.url().startsWith('blob:')) external.push(request.url()); });
  const modules = await context.request.get(`${url}/api/v1/capabilities/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
  await context.route('**/api/v1/capabilities/', route => route.fulfill({ json: { ...modules,
    microphone: { ...modules.microphone, available: true, state: 'ready', busy: false },
    conversation: { auto_start: false },
    voice: { ...modules.voice, provider: 'piper', cloud: false, can_request: true, busy: false, state: 'ready', max_characters: 2000 } } }));
  await context.route('**/api/v1/microphone/transcribe/', route => route.fulfill({ json: { text: heard.shift() ?? '', cloud: false } }));
  await context.route('**/api/v1/chat/', async route => {
    const { prompt } = route.request().postDataJSON(); prompts.push(prompt);
    await route.fulfill({ json: { reply: prompt === 'mejor dime la hora' ? 'Son las doce en punto.' : LONG, suggested_action: null } });
  });
  await context.route('**/api/v1/conversation/cancel/', route => route.fulfill({ json: { cancelled: true } }));
  await context.route('**/api/v1/voice/speech/', async route => {
    spoken.push(route.request().postDataJSON().text);
    await route.fulfill({ contentType: 'audio/wav', body: clip });
  });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.locator('#conversation-wake').uncheck();
  await page.locator('#session-button').click(); await page.locator('#token-input').fill(token);
  await page.locator('#session-form button[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('#conversation-enabled').disabled, { timeout: 30000 });
  assert(!(await page.locator('#conversation-barge').isDisabled()), 'El navegador debe ofrecer cancelación de eco.');
  await page.locator('#conversation-barge').check();
  await page.locator('#conversation-enabled').check();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.startsWith('Parte 1'), { timeout: 40000 });
  // El eco no debe cortar ni dejar sorda la escucha; la orden nueva sí reemplaza la respuesta.
  await page.waitForFunction(() => document.querySelector('#console-text').textContent === 'Son las doce en punto.', { timeout: 60000 });
  assert.deepEqual(prompts.slice(0, 2), ['cuéntame algo largo', 'mejor dime la hora']);
  // El saludo de inicio puede sonar antes; la respuesta larga se sintetiza frase a frase.
  assert.equal(spoken.find(text => text.startsWith('Parte')), 'Parte 1 del informe largo sobre la red local.',
    'la voz local empieza por la primera frase, no por la respuesta entera');
  assert(spoken.includes('Son las doce en punto.'), 'la orden nueva se responde en voz');
  assert.equal(heard.length, 0, 'se transcribieron el turno, el eco y la orden nueva');
  await page.screenshot({ path: resolve(artifacts, 'barge-in-desktop.png'), fullPage: true });
  await page.locator('#conversation-stop').click();
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log(`Interrupcion E2E correcta: Silero real, eco ignorado sin quedar sorda, orden nueva atendida; ${spoken.length} frases sintetizadas, IA/STT/TTS simulados.`);
} catch (error) {
  console.error('Estado:', { prompts, spoken: spoken.slice(0, 4), total: spoken.length, pending: heard, errors });
  throw error;
} finally { await browser.close(); }
