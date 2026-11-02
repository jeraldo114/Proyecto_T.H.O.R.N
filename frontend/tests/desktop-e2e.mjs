import assert from 'node:assert/strict';
import { installPrivateDiagnostics, waitForServer } from './private-api.mjs';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const url = process.env.THORN_TEST_URL || 'http://127.0.0.1:8005';
const python = process.env.THORN_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const authority = process.env.THORN_TEST_TOKEN || execFileSync(python, ['-B', '-c',
  'from thorn_web.config import prepare_environment; prepare_environment(); import os; print(os.environ["THORN_API_TOKEN"])'], { cwd: root, encoding: 'utf8' }).trim();
installPrivateDiagnostics(authority); await waitForServer(url);
const artifacts = resolve(root, '.artifacts/hud-visual'); await mkdir(artifacts, { recursive: true });
execFileSync(python, ['-B', '-c', [
  'import io', 'from pathlib import Path', 'from pypdf import PdfReader, PdfWriter',
  'from tools.verify_compiled import sample_pdf', 'r=PdfReader(io.BytesIO(sample_pdf()))',
  'w=PdfWriter()', 'w.add_page(r.pages[0])', 'w.add_page(r.pages[0])',
  'w.write(Path(".artifacts/hud-visual/prueba.pdf"))',
].join('\n')], { cwd: root });
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const browser = await chromium.launch({ executablePath: process.env.THORN_BROWSER || (existsSync(chrome) ? chrome : undefined),
  headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const summary = {}, errors = [], external = [];
const warnings = [];
let context, headers;
try {
  context = await browser.newContext({ viewport: { width: 1360, height: 820 } });
  await context.route('**/api/v1/capabilities/', async route => {
    const response = await route.fetch(), value = await response.json();
    await route.fulfill({ response, json: { ...value, conversation: { ...value.conversation, auto_start: false } } });
  });
  const hand = await readFile(resolve(root, '.artifacts/bastion9-fixtures/hand.png'));
  await context.addInitScript(encoded => {
    window.hudTestTracks = [];
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async options => {
      if (options.audio !== false) throw new Error('La prueba no abre microfono fisico');
      const image = new Image(); image.src = `data:image/png;base64,${encoded}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
      const draw = () => { canvas.getContext('2d').drawImage(image, 0, 0, 640, 480); requestAnimationFrame(draw); }; draw();
      const stream = canvas.captureStream(15); window.hudTestTracks.push(...stream.getTracks()); return stream;
    } });
  }, hand.toString('base64'));
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (['warning', 'error'].includes(message.type())) warnings.push(message.text()); });
  page.on('request', request => { if (!request.url().startsWith(url) && !/^(data|blob):/.test(request.url())) external.push(request.url()); });
  await page.goto(url); await page.waitForSelector('#core-canvas[data-rendered=true]');
  await page.screenshot({ path: resolve(artifacts, 'presencia.png') });
  const largeCore = await page.locator('#core-scene').boundingBox();
  assert.equal(await page.locator('body').getAttribute('data-desktop-mode'), 'presence');
  await page.locator('#core-scene').click(); assert(await page.locator('#radial-menu').isVisible());
  await page.screenshot({ path: resolve(artifacts, 'menu-radial.png') });
  await page.keyboard.press('Escape'); assert(!await page.locator('#radial-menu').isVisible());
  const issued = await context.request.post(`${url}/api/v1/auth/session/`, { headers: { Authorization: `Bearer ${authority}` }, data: {} });
  assert.equal(issued.status(), 200); const session = await issued.json(); headers = { Authorization: `Bearer ${session.access_token}` };
  await page.evaluate(value => window.dispatchEvent(new CustomEvent('thorn-native-session', { detail: value })), session);
  await page.waitForFunction(() => document.querySelector('#session-label').textContent === 'Conectado');
  await page.waitForFunction(() => document.querySelector('#hud-cpu').textContent !== '—');
  assert(!external.length, 'Presencia no hace solicitudes externas.');

  await page.locator('[data-view=network]').click(); await page.locator('#internet-consent').check();
  await page.locator('[data-tool=maps]').click();
  await page.locator('#b9-place').fill('Londres');
  await page.locator('#b9-map-form button').click();
  await page.locator('.panel-map').waitFor({ state: 'visible', timeout: 30000 });
  assert.equal(await page.locator('body').getAttribute('data-desktop-mode'), 'work');
  assert((await page.locator('#core-scene').boundingBox()).width < largeCore.width);
  const mapPanel = page.locator('.panel-map').last(); const mapId = await mapPanel.getAttribute('data-panel-id');
  const found = await context.request.get(`${url}/api/v1/workspace/`, { headers });
  const mapData = (await found.json()).panels.find(panel => panel.id === mapId).data;
  assert(mapData.places.some(place => /Londres/.test(place.title) && Number.isFinite(place.latitude)));
  assert.equal(external.length, 0, 'El mapa espera la solicitud explicita de cartografia.');
  const firstTile = page.waitForResponse(response => response.url().startsWith('https://tile.openstreetmap.org/') && response.status() === 200, { timeout: 30000 });
  await mapPanel.getByRole('button', { name: 'Cargar mapa · OpenStreetMap', exact: true }).click();
  await firstTile;
  await page.waitForFunction(() => [...document.querySelectorAll('.leaflet-tile')].some(image => image.complete && image.naturalWidth > 0));
  await page.screenshot({ path: resolve(artifacts, 'mapa.png') });
  const title = mapPanel.locator('.floating-header'), box = await title.boundingBox();
  await page.mouse.move(box.x + 110, box.y + 25); await page.mouse.down(); await page.mouse.move(box.x + 190, box.y + 55, { steps: 12 });
  const moved = page.waitForResponse(response => response.url().endsWith('/workspace/change/') && response.request().postDataJSON()?.operation === 'move');
  await page.mouse.up(); assert.equal((await moved).status(), 200);
  const afterDrag = await context.request.get(`${url}/api/v1/workspace/`, { headers });
  const mapPosition = (await afterDrag.json()).panels.find(panel => panel.id === mapId).position;
  assert.notDeepEqual(mapPosition, { x: .1, y: .1 });
  await title.focus();
  const keyboardMove = page.waitForResponse(response => response.url().endsWith('/workspace/change/') && response.request().postDataJSON()?.operation === 'move');
  await title.press('ArrowRight'); assert.equal((await keyboardMove).status(), 200);
  await mapPanel.locator('[data-window-action=maximize]').click(); assert(await mapPanel.evaluate(node => node.classList.contains('is-maximized')));
  await mapPanel.locator('[data-window-action=maximize]').click();
  summary.map = { real_coordinates: true, real_osm_tiles: true, consent_required: true, position_saved: true };

  await page.locator('[data-tool=documents]').click();
  await page.locator('#b9-file').setInputFiles(resolve(artifacts, 'prueba.pdf'));
  await page.locator('#b9-document-form button').click();
  const documentPanel = page.locator('.panel-document').last();
  await documentPanel.locator('.pdf-surface canvas[data-rendered=true]').waitFor({ timeout: 35000 });
  await documentPanel.locator('.pdf-surface canvas').screenshot({ path: resolve(artifacts, 'pdf-canvas.png') });
  const textPixels = Number(execFileSync(python, ['-c',
    'from PIL import Image;im=Image.open(".artifacts/hud-visual/pdf-canvas.png").convert("RGB");print(sum(max(im.getpixel((x,y)))<160 for y in range(im.height) for x in range(im.width)))'], { cwd: root, encoding: 'utf8' }).trim());
  await writeFile(resolve(artifacts, 'avisos-browser.json'), JSON.stringify(warnings));
  assert(textPixels > 60, `El PDF debe contener texto visible, no un canvas en blanco (${textPixels} pixels).`);
  const canvas = documentPanel.locator('.pdf-surface canvas'), firstWidth = await canvas.evaluate(node => node.width);
  await documentPanel.getByRole('button', { name: 'Página siguiente', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.panel-document .viewer-toolbar span')?.textContent === '2 / 2');
  await documentPanel.getByRole('button', { name: 'Ampliar PDF', exact: true }).click();
  await page.waitForFunction(width => document.querySelector('.panel-document .pdf-surface canvas[data-rendered=true]')?.width > width, firstWidth);
  await page.screenshot({ path: resolve(artifacts, 'documento.png') });
  const docId = await documentPanel.getAttribute('data-panel-id');
  await documentPanel.locator('[data-window-action=minimize]').click();
  await documentPanel.waitFor({ state: 'hidden' });
  await page.locator(`.task-chip[data-panel-id="${docId}"]`).click(); await documentPanel.waitFor({ state: 'visible' });
  summary.pdf = { pages: 2, visible_text_pixels: textPixels, worker_render: true, zoom: true, minimize_restore: true, scripts_executed: false };

  await page.locator('[data-tool=camera]').click();
  await page.locator('#b9-camera-consent').check(); await page.locator('#b9-camera-start').click();
  await page.waitForFunction(() => document.querySelector('#b9-vision-state').textContent === 'VISTA LOCAL');
  await page.locator('#b9-gestures').check();
  await page.waitForFunction(() => document.querySelector('#b9-vision-state').textContent === 'SEGUIMIENTO LOCAL ACTIVADO', { timeout: 25000 });
  await page.waitForFunction(() => /Mantén la palma|Cursor local|Pinza activa/.test(document.querySelector('#b9-gesture-state').textContent));
  await page.locator(`.task-chip[data-panel-id="${mapId}"]`).click();
  const gestureHeader = await mapPanel.locator('.floating-header').boundingBox();
  const viewport = page.viewportSize();
  const gesturePoint = { x: (gestureHeader.x + 100) / viewport.width, y: (gestureHeader.y + 25) / viewport.height };
  const gestureWrite = page.waitForResponse(response => response.url().endsWith('/workspace/change/') && response.request().postDataJSON()?.operation === 'move');
  await page.evaluate(point => {
    const send = value => window.dispatchEvent(new CustomEvent('thorn-gesture', { detail: value }));
    send({ state: 'dragging', dragging: true, ...point });
    send({ state: 'dragging', dragging: true, x: point.x + .03, y: point.y + .02 });
    send({ state: 'cursor', dragging: false, released: true, x: point.x + .03, y: point.y + .02 });
  }, gesturePoint);
  assert.equal((await gestureWrite).status(), 200);
  await page.locator('[data-tool=camera]').click();
  await page.locator('.panel-camera [data-window-action=close]').click();
  await page.waitForFunction(() => window.hudTestTracks.every(track => track.readyState === 'ended'));
  summary.camera = { detector_worker: true, synthetic_pinch_moves_panel: true, physical_camera_used: false, close_releases_tracks: true };
  await page.locator(`.task-chip[data-panel-id="${docId}"]`).click();
  await documentPanel.locator('[data-window-action=close]').click();
  await documentPanel.waitFor({ state: 'detached' });
  assert.deepEqual(errors, []);

  await page.locator('#focus-button').click();
  assert.equal(await page.locator('body').getAttribute('data-desktop-mode'), 'presence');
  await page.screenshot({ path: resolve(artifacts, 'presencia-conectada.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: resolve(artifacts, 'movil.png') });
  await page.setViewportSize({ width: 494, height: 454 });
  const clock = await page.locator('.clock').boundingBox(), core = await page.locator('#core-scene').boundingBox();
  assert(clock.x > core.x + core.width * .7, 'En ventana baja el reloj pasa a un lado.');
  await page.screenshot({ path: resolve(artifacts, 'compacto.png') });
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert(external.every(address => address.startsWith('https://tile.openstreetmap.org/')));
  assert.deepEqual(errors, []);
  summary.errors = errors; summary.private_storage = false;
  await writeFile(resolve(artifacts, 'verificacion.json'), JSON.stringify(summary, null, 2));
  console.log('HUD visual E2E correcto:', JSON.stringify(summary));
} finally {
  if (context && headers) await context.request.post(`${url}/api/v1/auth/revoke/`, { headers, data: {} }).catch(() => {});
  await browser.close();
}
