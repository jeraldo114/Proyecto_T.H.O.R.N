import assert from 'node:assert/strict';
import test from 'node:test';
import { GestureInterpreter } from '../src/gestures.js';
import { LocalCamera } from '../src/camera.js';
import { WorkspaceClient, operatorIntent, localControl } from '../src/workspace.js';
import { assistantState } from '../src/assistant-state.js';

function palm() {
  const points = Array.from({ length: 21 }, () => ({ x: 0.5, y: 0.6 }));
  for (const i of [8, 12, 16, 20]) points[i].y = 0.1;
  points[4] = { x: 0.1, y: 0.8 }; points[5].x = 0.2; points[17].x = 0.8;
  return points;
}
const video = () => ({ readyState: 4, srcObject: null, play: async () => {}, pause() {} });

test('gestos requieren palma sostenida y usan histeresis de pinza', () => {
  const gestures = new GestureInterpreter(), points = palm();
  assert.equal(gestures.update(points, 0).state, 'hold_palm');
  gestures.update(points, 200); gestures.update(points, 400);
  assert.equal(gestures.update(points, 500).dragging, false);
  assert.equal(gestures.update(points, 601).state, 'cursor');
  points[4] = { x: 0.51, y: 0.1 };
  assert.equal(gestures.update(points, 700).dragging, false);
  assert.equal(gestures.update(points, 790).dragging, true);
  points[4] = { x: 0.69, y: 0.1 };
  assert.equal(gestures.update(points, 850).dragging, true);
  points[4] = { x: 0.9, y: 0.1 };
  assert.equal(gestures.update(points, 900).released, false);
  assert.equal(gestures.update(points, 990).released, true);
  assert.equal(gestures.update([], 1500).state, 'waiting');
});

test('puntos malformados o tiempo no finito nunca activan una pinza', () => {
  const gestures = new GestureInterpreter();
  for (const points of [null, [], [{ x: 0, y: 0 }], Array(21).fill({ x: NaN, y: 0 })]) assert.equal(gestures.update(points, 1000).dragging, false);
  assert.equal(gestures.update(palm(), NaN).state, 'waiting');
});

test('camara exige consentimiento y corta un permiso que llega despues de detener', async () => {
  let resolve, stopped = 0, called = 0;
  const camera = new LocalCamera(video(), { getMedia: () => { called++; return new Promise(done => { resolve = done; }); } });
  await assert.rejects(camera.start(), /camera_consent_required/);
  assert.equal(called, 0);
  const pending = camera.start({ consent: true }); camera.stop();
  resolve({ getTracks: () => [{ stop: () => stopped++ }] }); await pending;
  assert.equal(stopped, 1); assert.equal(camera.stream, null); assert.equal(camera.state, 'off');
});

test('detener camara libera tracks, worker y objeto de video', async () => {
  let stopped = 0, terminated = 0, options;
  const track = { stop: () => stopped++ };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const element = video();
  const camera = new LocalCamera(element, { getMedia: async value => { options = value; return stream; },
    createWorker: () => ({ postMessage() {}, terminate: () => terminated++ }) });
  await camera.start({ consent: true }); await camera.enableGestures();
  assert.equal(options.audio, false); assert.equal(options.video.frameRate.max, 15);
  camera.stop(); assert.equal(stopped, 1); assert.equal(terminated, 1); assert.equal(element.srcObject, null);
});

test('un ImageBitmap tardio no pasa a un nuevo worker', async () => {
  let resolve, closed = 0, frames = 0;
  const camera = new LocalCamera(video(), { createImage: () => new Promise(done => { resolve = done; }) });
  camera.worker = { postMessage() { frames++; }, terminate() {} };
  const pending = camera.frame(camera.revision);
  camera.stopGestures(); camera.worker = { postMessage() { frames++; }, terminate() {} };
  resolve({ close: () => closed++ }); await pending; camera.stop();
  assert.equal(frames, 0); assert.equal(closed, 1);
});

test('una consulta de workspace que termina tras desconectar no restaura paneles', async () => {
  let resolve;
  const workspace = new WorkspaceClient(() => new Promise(done => { resolve = done; }));
  const request = workspace.refresh(); workspace.clear(); resolve({ panels: [{ id: 'a'.repeat(32) }] }); await request;
  assert.deepEqual(workspace.panels, []);
});

test('mapas y cancelacion se reconocen como peticiones completas', () => {
  assert.deepEqual(operatorIntent('muestra un mapa de Santiago'), { mode: 'map', value: 'Santiago' });
  assert.deepEqual(operatorIntent('¿T.H.O.R.N, muéstrame el mapa de Concepción?'), { mode: 'map', value: 'Concepción' });
  assert.equal(operatorIntent('explicame la frase muestra un mapa de Santiago'), null);
  assert.equal(localControl('¡Detén la respuesta!'), 'stop');
  assert.equal(localControl('T.H.O.R.N, silencio, por favor'), 'stop');
  assert.equal(localControl('qué significa silencio'), null);
});

test('los estados del nucleo no confunden un modelo listo con un microfono abierto', () => {
  const ready = { online: true, authenticated: true, conversation: 'off', microphone: 'idle' };
  assert.equal(assistantState(ready), 'idle');
  assert.equal(assistantState({ ...ready, conversation: 'listening' }), 'listening');
  assert.equal(assistantState({ ...ready, playing: true, busy: true }), 'speaking');
  assert.equal(assistantState({ ...ready, playbackState: 'blocked' }), 'blocked');
  assert.equal(assistantState({ ...ready, conversation: 'turn_error' }), 'error');
  assert.equal(assistantState({ ...ready, online: false, playing: true }), 'offline');
});
