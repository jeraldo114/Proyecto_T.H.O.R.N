import assert from 'node:assert/strict';
import { installPrivateDiagnostics } from './private-api.mjs';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, createHash } from 'node:crypto';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8002';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const ownServer = process.env.THORN_TEST_START_SERVER === '1';
let server;
let token;
if (ownServer) {
  // El servidor aislado de CI usa claves efimeras y no requiere MySQL.
  assert.notEqual(process.platform, 'win32', 'En Windows ejecutar serve.py antes del test E2E.');
  token = randomBytes(40).toString('hex');
  server = spawn(python, ['-B', 'serve.py', '--sin-db', '--port', new URL(url).port], {
    cwd: root, stdio: 'ignore',
    env: { ...process.env, DJANGO_SECRET_KEY: randomBytes(40).toString('hex'), THORN_API_TOKEN: token, THORN_SYSTEM_SENSOR: '1', THORN_SENSOR_INTERVAL: '2', THORN_VOICE_ENABLED: '0' },
  });
} else {
  token = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c', 'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
}
installPrivateDiagnostics(token);
let browser;
const artifacts = resolve(root, '.artifacts');
try {
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try { ready = (await fetch(`${url}/api/v1/health/`)).ok; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  assert(ready, 'El backend debe estar disponible.');
  const systemChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const executablePath = process.env.THORN_BROWSER || (process.platform === 'win32' && existsSync(systemChrome) ? systemChrome : undefined);
  browser = await chromium.launch({ executablePath, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await context.route('**/api/v1/capabilities/', async route => {
    const response = await route.fetch(), data = await response.json();
    await route.fulfill({ response, json: { ...data, conversation: { ...data.conversation, auto_start: false },
      voice: { ...data.voice, provider: 'elevenlabs', cloud: true, can_request: false, state: 'disabled' } } });
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url);
  await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.locator('#cpu-value').innerText(), '—%');
  await page.locator('#session-button').click();
  await page.locator('#token-input').fill('incorrect-token-for-test');
  await page.locator('#session-form button[type=submit]').click();
  await page.waitForFunction(() => document.querySelector('#session-error').textContent.includes('incorrecto'));
  await page.locator('#token-input').fill(token);
  await page.locator('#session-form button[type=submit]').click();
  await page.waitForFunction(() => document.querySelector('#sample-state').textContent === 'MUESTRA VIGENTE');
  assert.equal(await page.locator('#token-input').inputValue(), '');
  assert(!await page.content().then(content => content.includes(token)));
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal((await context.cookies()).length, 0);

  const source = await context.request.get(`${url}/api/v1/system/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
  assert(source.sensor.fresh && source.snapshot.memory_total_bytes > 0);
  const pixels = await page.evaluate(() => {
    const canvas = document.querySelector('#core-canvas');
    const gl = canvas.getContext('webgl2');
    const data = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, data);
    let visible = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 20 && data[i] + data[i + 1] + data[i + 2] > 80) visible++;
    return visible;
  });
  assert(pixels > 1000, 'El canvas 3D no debe estar vacio.');
  const hash = async () => createHash('sha256').update(await page.locator('#core-canvas').screenshot()).digest('hex');
  const before = await hash(); await page.waitForTimeout(700); assert.notEqual(await hash(), before, 'El nucleo debe moverse.');
  await page.locator('[data-view=activity]').click();
  await page.locator('#pause-button').click();
  assert.equal(await page.locator('#sample-state').innerText(), 'MUESTRA RETENIDA');
  const count = await page.locator('#sample-count').innerText();
  const clock = await page.locator('#clock-time').innerText();
  await page.waitForTimeout(2400);
  assert.equal(await page.locator('#sample-count').innerText(), count);
  assert.notEqual(await page.locator('#clock-time').innerText(), clock);
  await page.locator('#pause-button').click();
  await page.waitForFunction(() => document.querySelector('#sample-state').textContent === 'MUESTRA VIGENTE');
  await page.locator('[data-view=system]').click();
  await page.locator('#diagnostic-button').click();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('aceptado'));
  await page.locator('#close-console').click();
  await page.locator('[data-view=activity]').click();
  await page.locator('#activity-filter').selectOption('command');
  await page.waitForFunction(() => document.querySelector('#activity-list').textContent.includes('Comando recibido'));
  await page.locator('#activity-filter').selectOption('all');
  await page.locator('#command-input').fill('estado');
  await page.locator('#command-form').evaluate(form => form.requestSubmit());
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('Núcleo: running'));
  await page.locator('#close-console').click();

  await mkdir(artifacts, { recursive: true });
  await page.locator('[data-view=system]').click();
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#report-button').click()]);
  const reportPath = resolve(artifacts, 'informe-test.json');
  await download.saveAs(reportPath);
  const reportText = await readFile(reportPath, 'utf8');
  assert(!reportText.includes(token));
  const report = JSON.parse(reportText);
  assert(report.telemetry.snapshot.sampled_at && report.observed_history.length > 0);
  await page.locator('[data-view=system]').click(); assert(await page.locator('#system-table').isVisible());
  await page.locator('[data-view=activity]').click(); assert(await page.locator('#event-table').isVisible());
  const capabilities = await context.request.get(`${url}/api/v1/capabilities/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
  assert.equal(capabilities.voice.provider, 'piper');
  assert.equal(capabilities.voice.cloud, false);
  assert.equal(capabilities.voice.microphone, false);
  await page.locator('[data-view=assistant]').click();
  await page.waitForSelector('.rule-row');
  assert.equal(await page.locator('.rule-row').count(), 3);
  assert(await page.locator('#voice-test-button').isDisabled(), 'La voz exige consentimiento aunque este configurada.');
  assert.equal(await page.locator('#voice-consent').isChecked(), false);

  // Prueba de protocolo UI SIMULADA: intercepta TODA sintesis, no consume creditos.
  let voiceRequests = 0, voiceMode = 'invalid';
  // Frames MPEG-1 Layer III con granulos vacios: silencio, sin grabaciones ajenas.
  const silentMP3 = Buffer.alloc(417 * 100);
  for (let offset = 0; offset < silentMP3.length; offset += 417) silentMP3.writeUInt32BE(0xfffb9064, offset);
  const voiceFixture = process.env.THORN_VOICE_FIXTURE_MP3;
  const voiceMP3 = voiceFixture ? await readFile(voiceFixture) : silentMP3;
  assert(voiceMP3.length > 0 && voiceMP3.length <= 2 * 1024 * 1024);
  if (voiceFixture) {
    const rms = await page.evaluate(async encoded => {
      const context = new AudioContext();
      try {
        const bytes = Uint8Array.from(atob(encoded), value => value.charCodeAt(0));
        const audio = await context.decodeAudioData(bytes.buffer);
        const samples = audio.getChannelData(0);
        return Math.sqrt(samples.reduce((total, value) => total + value * value, 0) / samples.length);
      } finally { await context.close(); }
    }, voiceMP3.toString('base64'));
    assert(rms > 0.002, 'La muestra guardada debe contener una senal real, no silencio.');
  }
  await context.route('**/api/v1/voice/speech/', async route => {
    voiceRequests++;
    const body = route.request().postDataJSON();
    assert.equal(body.cloud_consent, true);
    assert(!JSON.stringify(body).includes(token));
    if (voiceMode === 'audio') {
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: voiceMP3 });
    } else if (voiceMode === 'pending') {
      await new Promise(resolve => setTimeout(resolve, 600));
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"voice_provider_unavailable"}' }).catch(() => {});
    } else await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await context.route('**/api/v1/capabilities/', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ ...capabilities,
      conversation: { ...capabilities.conversation, auto_start: false },
      voice: { ...capabilities.voice, provider: 'elevenlabs', cloud: true, state: 'configured', available: true, can_request: true, busy: false } }),
  }));
  await page.locator('#refresh-button').evaluate(button => button.click());
  await page.waitForFunction(() => document.querySelector('#voice-state').textContent === 'Configurada / sin probar');
  assert.equal(voiceRequests, 0, 'Consultar y abrir vistas no debe sintetizar nada.');
  assert.equal(await page.locator('#voice-auto').isChecked(), false);
  assert(await page.locator('#voice-auto').isDisabled());
  await page.locator('#speaker-test-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Tono local finalizado');
  assert.equal(voiceRequests, 0, 'Comprobar altavoces nunca contacta ElevenLabs.');
  await page.locator('#voice-consent').check();
  await page.locator('#voice-text').fill('Prueba simulada del protocolo de voz.');
  await page.locator('#voice-test-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent.includes('audio válido'));
  assert.equal(voiceRequests, 1);
  assert(await page.locator('#voice-stop-button').isDisabled());
  voiceMode = 'pending';
  await page.locator('#voice-test-button').click();
  await page.waitForFunction(() => !document.querySelector('#voice-stop-button').disabled);
  await page.locator('#voice-stop-button').click();
  assert.equal(await page.locator('#voice-result').innerText(), 'Detenido');
  await page.waitForTimeout(800);
  assert.equal(await page.locator('#voice-result').innerText(), 'Detenido', 'Una respuesta tardia no debe reactivar la voz.');
  voiceMode = 'audio';
  await page.evaluate(() => {
    const revoke = URL.revokeObjectURL.bind(URL);
    window.testVoiceRevoked = 0;
    URL.revokeObjectURL = url => { window.testVoiceRevoked++; revoke(url); };
  });
  await page.locator('#voice-test-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Reproduciendo / ElevenLabs');
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Reproducción terminada');
  assert.equal(await page.evaluate(() => window.testVoiceRevoked), 1, 'Debe revocar la URL del audio al terminar.');
  assert(await page.locator('#voice-stop-button').isDisabled());

  // Un bloqueo de play() conserva el mismo audio, no repite una sintesis de pago.
  await page.evaluate(() => {
    const play = HTMLMediaElement.prototype.play;
    window.testPlaybackBlocked = true;
    HTMLMediaElement.prototype.play = function () {
      if (window.testPlaybackBlocked) return Promise.reject(new DOMException('test', 'NotAllowedError'));
      window.testPlayedVolume = this.volume;
      return play.call(this);
    };
    const slider = document.querySelector('#voice-volume');
    slider.value = '65'; slider.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.locator('#voice-test-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent.includes('bloqueada'));
  const beforeResume = voiceRequests;
  assert(await page.locator('#voice-resume-button').isVisible());
  await page.evaluate(() => { window.testPlaybackBlocked = false; });
  await page.locator('#voice-resume-button').click();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Reproduciendo / ElevenLabs');
  assert.equal(voiceRequests, beforeResume);
  assert.equal(await page.evaluate(() => window.testPlayedVolume), 0.65);
  await page.locator('#voice-stop-button').click();

  // Respuestas SIMULADAS: verifica el opt-in sin inferencia ni cargos remotos.
  let chatMode = 'ok';
  await context.route('**/api/v1/chat/', async route => {
    const mode = chatMode;
    if (mode === 'pending') await new Promise(resolve => setTimeout(resolve, 600));
    if (mode === 'error') {
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"model_query_failed"}' });
    } else await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      reply: mode === 'long' ? 'x'.repeat(2001) : 'Respuesta simulada de la prueba de voz.',
      suggested_action: null, executed: false,
    }) });
  });
  const query = async () => {
    await page.locator('#command-input').fill('Consulta simulada de voz');
    await page.locator('#command-input').press('Enter');
  };
  await query();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent === 'Respuesta simulada de la prueba de voz.');
  assert.equal(voiceRequests, beforeResume, 'Consentir voz manual no activa la lectura automatica.');
  await page.locator('#close-console').click();
  await page.locator('#voice-auto').check();
  await query();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent === 'Reproduciendo / ElevenLabs');
  assert.equal(voiceRequests, beforeResume + 1);
  await page.locator('#stop-reply-button').click();
  await page.locator('#close-console').click();

  const beforeOptOut = voiceRequests;
  chatMode = 'pending';
  await query();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('Consultando'));
  await page.locator('#voice-auto').uncheck();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent === 'Respuesta simulada de la prueba de voz.');
  assert.equal(voiceRequests, beforeOptOut, 'Retirar lectura durante la consulta no envia la respuesta.');
  await page.locator('#close-console').click();

  // Activarla durante una consulta ya pendiente tampoco envia esa respuesta.
  await query();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('Consultando'));
  await page.locator('#voice-auto').check();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent === 'Respuesta simulada de la prueba de voz.');
  assert.equal(voiceRequests, beforeOptOut);
  await page.locator('#close-console').click();

  chatMode = 'long'; await query();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent.includes('2000 caracteres'));
  assert.equal(voiceRequests, beforeOptOut, 'No trunca ni envia automaticamente textos demasiado largos.');
  await page.locator('#close-console').click();
  chatMode = 'error'; await query();
  await page.waitForFunction(() => document.querySelector('#console-text').textContent.includes('respuesta válida'));
  assert.equal(voiceRequests, beforeOptOut, 'Los errores no se envian al proveedor de voz.');
  await page.locator('#close-console').click();
  chatMode = 'ok'; voiceMode = 'pending';
  await query();
  await page.waitForFunction(() => document.querySelector('#voice-result').textContent.includes('Sintetizando'));
  await page.locator('#voice-consent').uncheck();
  await page.waitForTimeout(800);
  assert.equal(await page.locator('#voice-auto').isChecked(), false);
  assert.equal(await page.locator('#voice-result').innerText(), 'Autorización de voz retirada');
  assert(await page.locator('#voice-stop-button').isDisabled());
  assert.equal(voiceRequests, beforeOptOut + 1, 'Retirar autorizacion no genera otro intento.');
  await page.locator('#close-console').click();
  await context.unroute('**/api/v1/chat/');
  await page.locator('#voice-consent').uncheck();
  assert.equal(await page.locator('#voice-auto').isChecked(), false);
  assert(await page.locator('#voice-test-button').isDisabled());
  await context.unroute('**/api/v1/capabilities/');
  await page.locator('#refresh-button').evaluate(button => button.click());
  await page.waitForFunction(state => document.querySelector('#voice-state').textContent !== 'Configurada / sin probar' || state === 'configured', capabilities.voice.state);
  if (capabilities.actions.enabled) {
    let confirmations = 0;
    const observe = request => { if (request.url().includes('/actions/confirm/')) confirmations++; };
    page.on('request', observe);
    await page.locator('#action-select').selectOption('open_notepad');
    await page.locator('#prepare-action-button').click();
    await page.waitForSelector('#action-dialog[open]');
    assert((await page.locator('#action-confirm-label').innerText()).includes('Bloc de notas'));
    await page.locator('#cancel-action-button').click();
    assert.equal(confirmations, 0, 'Preparar y cancelar no debe ejecutar una accion.');
    page.off('request', observe);
  } else assert(await page.locator('#prepare-action-button').isDisabled());
  await page.locator('[data-view=history]').click();
  if (capabilities.audit.enabled) {
    await page.waitForSelector('.history-row');
    const notesBefore = await context.request.get(`${url}/api/v1/memory/`, { headers: { Authorization: `Bearer ${token}` } }).then(response => response.json());
    const note = `Nota temporal E2E ${randomBytes(8).toString('hex')}`;
    if (notesBefore.notes.length < notesBefore.capacity) {
      let noteId;
      try {
        const saved = await context.request.post(`${url}/api/v1/memory/save/`, { headers: { Authorization: `Bearer ${token}` }, data: { text: note } }).then(response => response.json());
        assert(saved.saved); noteId = saved.id;
        await page.locator('[data-view=central]').click(); await page.locator('[data-view=history]').click();
        await page.waitForFunction(note => document.querySelector('#memory-list').textContent.includes(note), note);
      } finally {
        if (noteId) {
          const deleted = await context.request.post(`${url}/api/v1/memory/delete/`, { headers: { Authorization: `Bearer ${token}` }, data: { id: noteId } }).then(response => response.json());
          assert(deleted.deleted);
        }
      }
    }
  } else await page.waitForFunction(() => document.querySelector('#history-table').textContent.includes('MySQL'));
  await page.locator('[data-view=assistant]').click();
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Desborde de asistente a ${width}px`);
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: resolve(artifacts, 'hud-assistant.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifacts, 'hud-voice-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('[data-view=central]').click();
  await page.locator('#focus-button').click(); assert(!await page.locator('.telemetry-column').isVisible()); await page.locator('#focus-button').click();
  await page.locator('#settings-button').click(); await page.locator('#poll-interval').selectOption('5000'); await page.locator('#close-settings').click();

  for (const width of [320, 390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 960 });
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Desborde a ${width}px`);
    assert(await page.locator('#core-canvas').isVisible());
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#mobile-settings-button').click(); assert(await page.locator('#settings-dialog').isVisible()); await page.locator('#close-settings').click();
  await page.screenshot({ path: resolve(artifacts, 'hud-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: resolve(artifacts, 'hud-desktop.png'), fullPage: true });

  // Un fallo de red debe invalidar la muestra, sin inventar sustitutos.
  await context.route('**/api/v1/status/', route => route.abort());
  await page.locator('[data-view=system]').click();
  await page.locator('#refresh-button').click();
  await page.waitForFunction(() => document.querySelector('#connection-label').textContent === 'SIN ENLACE');
  assert.equal(await page.locator('#sample-state').innerText(), 'MUESTRA NO VIGENTE');
  await context.unroute('**/api/v1/status/');
  await page.locator('#session-button').click(); await page.locator('#disconnect-button').click();
  assert.equal(await page.locator('#voice-consent').isChecked(), false);
  assert.equal(await page.locator('#voice-auto').isChecked(), false);
  assert.equal(await page.locator('#voice-text').inputValue(), '');
  assert(await page.locator('#speak-reply-button').isDisabled());
  assert.equal(await page.locator('#cpu-value').innerText(), '—%');
  assert.equal(await page.locator('#process-count').innerText(), '—');
  assert.deepEqual(errors, []);
  console.log('HUD E2E correcto: datos reales, 3D, privacidad, acciones, memoria y responsive; voz manual/automatica opt-in, reanudacion sin duplicar, tono local y cancelacion simulados, sin cargos remotos.');
} finally {
  await browser?.close();
  if (server) {
    server.kill('SIGINT');
    await new Promise(resolve => { server.once('exit', resolve); setTimeout(() => { server.kill('SIGKILL'); resolve(); }, 5000).unref(); });
  }
}
