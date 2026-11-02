import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { visibleCorePixels } from './core-pixels.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8004';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const token = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(token); await waitForServer(url);
const fixturePath = process.env.THORN_VOICE_FIXTURE_WAV || process.env.THORN_VOICE_FIXTURE_MP3;
assert(fixturePath, 'Requiere audio de prueba ya disponible; no genera audio de pago.');
const clip = await readFile(fixturePath);
const fixtureMime = fixturePath.toLowerCase().endsWith('.wav') ? 'audio/wav' : 'audio/mpeg';
const executablePath = process.env.THORN_BROWSER || (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
const artifacts = resolve(root, '.artifacts'); await mkdir(artifacts, { recursive: true });
const decoder = await chromium.launch({ executablePath, headless: true });
let samples;
try {
  const tab = await decoder.newPage();
  samples = await tab.evaluate(async encoded => {
    const context = new AudioContext({ sampleRate: 16000 });
    try {
      const buffer = await context.decodeAudioData(Uint8Array.from(atob(encoded), char => char.charCodeAt(0)).buffer);
      // Silencio inicial y final hacen medible el corte por VAD, no por boton.
      return [...new Float32Array(16000), ...buffer.getChannelData(0).slice(0, 16000 * 6), ...new Float32Array(32000)];
    } finally { await context.close(); }
  }, clip.toString('base64'));
} finally { await decoder.close(); }
const wav = Buffer.alloc(44 + samples.length * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
wav.write('data', 36); wav.writeUInt32LE(samples.length * 2, 40);
samples.forEach((value, i) => wav.writeInt16LE(Math.round(Math.max(-1, Math.min(1, value)) * 32767), 44 + i * 2));
const fixture = resolve(artifacts, 'conversation-fixture.wav'); await writeFile(fixture, wav);
const browser = await chromium.launch({ executablePath, headless: true,
  args: ['--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${fixture}`, '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
let diagnosticPage, diagnostics = [], counters = { uploads: 0, chats: 0, speech: 0 };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.grantPermissions(['microphone'], { origin: url });
  const page = await context.newPage(), errors = [], external = [];
  diagnosticPage = page;
  page.on('console', message => { if (message.type() === 'error') diagnostics.push(message.text().slice(0, 500)); });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!request.url().startsWith(url) && !request.url().startsWith('blob:')) external.push(request.url()); });
  await page.addInitScript(() => {
    window.testStreams = [];
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async options => {
      const stream = await original(options); window.testStreams.push(stream); return stream;
    };
    window.testVoiceLevels = [];
    setInterval(() => { const canvas = document.querySelector('#core-canvas'); if (canvas) window.testVoiceLevels.push(Number(canvas.dataset.voiceLevel || 0)); }, 50);
  });
  const closed = () => page.evaluate(() => window.testStreams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
  let uploads = 0, chats = 0, speech = 0, pendingSpeech, wakeTexts;
  const modules = await context.request.get(`${url}/api/v1/capabilities/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
  await context.route('**/api/v1/capabilities/', route => route.fulfill({ json: { ...modules,
    microphone: { ...modules.microphone, available: true, state: 'ready', busy: false },
    conversation: { auto_start: true }, voice: { ...modules.voice, provider: 'elevenlabs', cloud: true, can_request: true, busy: false } } }));
  await context.route('**/api/v1/microphone/transcribe/', async route => {
    uploads++; counters.uploads = uploads; assert(await closed(), 'Tracks cerrados antes de enviar PCM.');
    const pcm = route.request().postDataBuffer(); assert(pcm.length >= 3200 && pcm.length <= 480000);
    assert.equal(route.request().headers()['x-thorn-microphone-consent'], 'local');
    if (process.env.THORN_MIC_LIVE === '1') await route.continue();
    else await route.fulfill({ json: { text: wakeTexts?.shift() ?? 'consulta de prueba', cloud: false } });
  });
  await context.route('**/api/v1/chat/', async route => {
    chats++; counters.chats = chats; assert(await closed(), 'Sin captura mientras responde el modelo.');
    await route.fulfill({ json: { reply: 'Respuesta simulada para verificar turnos.', suggested_action: null } });
  });
  await context.route('**/api/v1/voice/speech/', async route => {
    speech++; counters.speech = speech; assert(await closed(), 'Sin captura durante ElevenLabs.');
    assert.equal(route.request().postDataJSON().cloud_consent, true);
    if (pendingSpeech) await pendingSpeech;
    await route.fulfill({ contentType: fixtureMime, body: clip });
  });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.locator('#conversation-wake').uncheck(); // Este flujo verifica conversación continua; el filtro por nombre tiene su suite propia.
  assert.equal(await page.evaluate(() => window.testStreams.length), 0);
  await page.locator('#session-button').click(); await page.locator('#token-input').fill(token);
  await page.locator('#session-form button[type=submit]').click();
  await page.waitForFunction(() => document.querySelector('#conversation-bar').dataset.state === 'listening', { timeout: 30000 });
  assert(await page.locator('#conversation-enabled').isChecked(), 'Se activa al conectar sin pulsar dictado.');
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.startsWith('Respuesta simulada'), { timeout: 30000 });
  assert.equal(speech, 0, 'Escuchar no autoriza TTS en nube.'); assert(chats > 0 && uploads > 0);
  assert(await page.locator('#microphone-text').inputValue());
  await page.waitForFunction(() => window.testStreams.length >= 2);
  await page.locator('#conversation-stop').click(); assert(await closed());
  // Mismo Silero y dispositivo sintético, ahora con filtro por nombre.
  const chatsBeforeWake = chats;
  wakeTexts = ['una charla ambiental', 'Thorn, consulta de prueba'];
  await page.locator('#conversation-wake').check();
  const ambient = page.waitForResponse(response => response.url().endsWith('/microphone/transcribe/'));
  await page.locator('#conversation-enabled').check(); await ambient;
  await page.waitForFunction(() => document.querySelector('#conversation-bar').dataset.state === 'waiting_for_wake');
  assert.equal(chats, chatsBeforeWake, 'El audio ambiental no consulta la IA.');
  assert.notEqual(await page.locator('#microphone-text').inputValue(), 'una charla ambiental');
  const activated = await page.waitForRequest(request => request.url().endsWith('/chat/'), { timeout: 30000 });
  assert.equal(activated.postDataJSON().prompt, 'consulta de prueba');
  await page.locator('#conversation-stop').click(); assert(await closed());
  await page.locator('#conversation-wake').uncheck(); wakeTexts = null;
  await page.locator('[data-view=assistant]').click();
  await page.locator('#voice-consent').check(); await page.locator('#voice-auto').check();
  await page.locator('#conversation-enabled').check();
  await page.locator('[data-view=central]').click();
  assert(await page.locator('#conversation-enabled').isChecked(), 'Cambiar de vista no corta modo conversacion.');
  await page.waitForFunction(() => document.querySelector('#core-canvas').dataset.assistantState === 'speaking' && document.querySelector('#core-canvas').dataset.voiceLevel > 0.005, { timeout: 30000 });
  assert.equal(speech, 1); assert(await closed());
  await page.screenshot({ path: resolve(artifacts, 'conversation-desktop.png'), fullPage: true });
  await page.waitForTimeout(600);
  assert(new Set(await page.evaluate(() => window.testVoiceLevels.filter(value => value > 0.005))).size > 3, 'Nivel variable segun waveform real.');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('#core-canvas').dataset.voiceLevel > 0.005);
  const pixels = await visibleCorePixels(page);
  assert(pixels > 300, 'Nucleo movil no vacio durante voz.');
  await page.screenshot({ path: resolve(artifacts, 'conversation-mobile-speaking.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  const priorStreams = await page.evaluate(() => window.testStreams.length);
  await page.waitForFunction(previous => window.testStreams.length > previous, priorStreams, { timeout: 20000 });
  await page.locator('#conversation-stop').click(); assert(await closed());
  await page.waitForFunction(() => Number(document.querySelector('#core-canvas').dataset.voiceLevel) < 0.002);
  assert.equal(speech, 1, 'Un turno produce solo una sintesis.');

  // Detener durante una sintesis tardia no reproduce ni reactiva entrada.
  let release; pendingSpeech = new Promise(resolve => { release = resolve; });
  await page.locator('#conversation-enabled').check();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent.includes('Sintetizando'), { timeout: 30000 });
  await page.locator('#conversation-stop').click(); release(); pendingSpeech = null;
  await page.waitForTimeout(700);
  assert(await closed()); assert(!await page.locator('#conversation-enabled').isChecked());
  assert.equal(await page.locator('#voice-result').textContent(), 'Conversación detenida');
  assert.equal(await page.locator('#core-canvas').getAttribute('data-voice-level'), '0.000');
  for (const width of [320, 390, 768, 1440, 1920]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 960 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Sin overflow en ${width}.`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifacts, 'conversation-mobile.png'), fullPage: true });
  // Ocultar detiene captura; mostrarla no vuelve a abrir el dispositivo.
  await page.locator('#conversation-enabled').check();
  await page.waitForFunction(() => document.querySelector('#conversation-bar').dataset.state === 'listening');
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
  assert(await closed()); assert(!await page.locator('#conversation-enabled').isChecked());
  const hiddenStreams = await page.evaluate(() => window.testStreams.length);
  await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForTimeout(2200); assert.equal(await page.evaluate(() => window.testStreams.length), hiddenStreams);
  // Un permiso denegado exige una nueva accion explicita, nunca un reintento.
  await page.evaluate(() => { window.testDeniedCalls = 0; navigator.mediaDevices.getUserMedia = async () => { window.testDeniedCalls++; throw new DOMException('test', 'NotAllowedError'); }; });
  await page.locator('#conversation-enabled').check();
  await page.waitForFunction(() => document.querySelector('#conversation-bar').dataset.state === 'permission_required');
  await page.waitForTimeout(2200);
  assert.equal(await page.evaluate(() => window.testDeniedCalls), 1); assert(await closed());
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal((await context.cookies()).length, 0); assert.deepEqual(external, []); assert.deepEqual(errors, []);
  console.log(`Conversacion E2E correcta: Silero real, ${process.env.THORN_MIC_LIVE === '1' ? 'Vosk real' : 'STT simulado'}, turnos seriales, autoarranque, RMS, cancelacion y responsive; IA/TTS simuladas, sin cargos.`);
} catch (error) {
  console.error('Estado de prueba:', await diagnosticPage?.locator('#conversation-state').textContent());
  console.error('Errores del navegador:', diagnostics);
  console.error('Turnos:', counters);
  await diagnosticPage?.locator('#conversation-stop').click().catch(() => {});
  console.error('Diagnostico VAD:', await diagnosticPage?.evaluate(async () => {
    let capture;
    let level = 0, frames = 0, probability = 0, starts = 0, messages = 0;
    try {
      const module = await import('/hud/assets/conversation-vad.js');
      capture = new module.ConversationCapture({ onLevel(value) { level = Math.max(level, value.level); frames++; }, onSpeechStart() { starts++; }, onSpeechEnd(pcm) { pcm.fill(0); }, onEnded() {} });
      await capture.start();
      const instances = capture.vad.getAudioInstances();
      const portHandler = instances.vadNode.port.onmessage;
      instances.vadNode.port.onmessage = event => { messages++; portHandler(event); };
      const original = capture.vad.options.onFrameProcessed;
      capture.vad.options.onFrameProcessed = (probs, frame) => { probability = Math.max(probability, probs.isSpeech); original(probs, frame); };
      await new Promise(resolve => setTimeout(resolve, 7000));
      const inference = await Promise.race([capture.vad.model.process(new Float32Array(512)).then(() => 'ok'), new Promise(resolve => setTimeout(() => resolve('timeout'), 2000))]);
      return { level, frames, probability, starts, messages, inference, context: capture.context.state, accepting: capture.accepting, closed: capture.closed, stream: instances.stream.getAudioTracks().map(track => ({ state: track.readyState, enabled: track.enabled, muted: track.muted })), connections: instances.vadNode.numberOfInputs };
    }
    catch (failure) { return String(failure.stack).slice(0, 1200); }
    finally { capture?.stop(); }
  }));
  throw error;
} finally { await browser.close(); }
