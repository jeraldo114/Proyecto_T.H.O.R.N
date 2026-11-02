import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { VoicePlayback } from '../src/voice-playback.js';

const originalAudio = globalThis.Audio;
const originalContext = globalThis.AudioContext;
const originalRevoke = URL.revokeObjectURL;
let instances, revoked, play;

class FakeAudio extends EventTarget {
  constructor(url) { super(); this.url = url; this.volume = 1; instances.push(this); }
  play() { return play(); }
  pause() { this.paused = true; }
  removeAttribute(name) { if (name === 'src') this.removed = true; }
  load() { this.loaded = true; }
}

beforeEach(() => {
  instances = []; revoked = []; play = async () => {};
  globalThis.Audio = FakeAudio;
  URL.revokeObjectURL = url => { revoked.push(url); originalRevoke.call(URL, url); };
});
afterEach(() => {
  globalThis.Audio = originalAudio;
  globalThis.AudioContext = originalContext;
  URL.revokeObjectURL = originalRevoke;
});

test('no abre salida de audio al construir el reproductor', () => {
  const player = new VoicePlayback(() => {});
  assert.equal(player.active, false);
  assert.equal(instances.length, 0);
});

test('conserva audio bloqueado y reanuda sin recrear el MP3', async () => {
  const states = [];
  const player = new VoicePlayback(state => states.push(state));
  play = async () => { throw new DOMException('blocked', 'NotAllowedError'); };
  await player.start(new Blob(['test'], { type: 'audio/mpeg' }));
  const url = player.url;
  assert.equal(player.state, 'blocked');
  assert.equal(player.active, true);
  assert.equal(revoked.length, 0);
  play = async () => {};
  await player.resume();
  assert.equal(player.state, 'playing');
  assert.equal(instances.length, 1);
  assert.equal(player.url, url);
  player.stop();
  assert.deepEqual(revoked, [url]);
  assert(states.includes('blocked'));
});

test('detener invalida una promesa play tardia y libera la URL', async () => {
  let finish;
  play = () => new Promise(resolve => { finish = resolve; });
  const player = new VoicePlayback(() => {});
  const pending = player.start(new Blob(['test']));
  const audio = instances[0];
  player.stop();
  finish(); await pending;
  assert.equal(player.state, 'idle');
  assert.equal(player.active, false);
  assert(audio.paused && audio.removed && audio.loaded);
  assert.equal(revoked.length, 1);
});

test('terminar y error tardio no liberan dos veces ni alteran otro audio', async () => {
  const player = new VoicePlayback(() => {});
  await player.start(new Blob(['first']));
  const first = instances[0];
  first.dispatchEvent(new Event('ended'));
  assert.equal(player.state, 'ended');
  await player.start(new Blob(['second']));
  first.dispatchEvent(new Event('error'));
  assert.equal(player.state, 'playing');
  assert.equal(revoked.length, 1);
  player.stop();
});

test('volumen se aplica al audio real, incluso durante su reproduccion', async () => {
  const player = new VoicePlayback(() => {});
  player.setVolume(0.5);
  await player.start(new Blob(['test']));
  assert.equal(instances[0].volume, 0.5);
  player.setVolume(0.25);
  assert.equal(instances[0].volume, 0.25);
  player.setVolume(NaN); player.setVolume(2);
  assert.equal(player.volume, 0.25);
  player.stop();
});

test('un formato no reproducible no deja audio o URL retenidos', async () => {
  const player = new VoicePlayback(() => {});
  play = async () => { throw new DOMException('bad media', 'NotSupportedError'); };
  await player.start(new Blob(['test']));
  assert.equal(player.state, 'playback_error');
  assert.equal(player.active, false);
  assert.equal(revoked.length, 1);
});

test('cancelar la prueba de salida durante resume no inicia un tono tarde', async () => {
  let finish, context;
  globalThis.AudioContext = class {
    constructor() { context = this; }
    resume() { return new Promise(resolve => { finish = resolve; }); }
    close() { this.closed = true; return Promise.resolve(); }
    createGain() { assert.fail('No iniciar audio tras cancelar'); }
  };
  const player = new VoicePlayback(() => {});
  const pending = player.testOutput();
  player.stop(); finish(); await pending;
  assert.equal(context.closed, true);
  assert.equal(player.active, false);
});

test('el nucleo recibe RMS de muestras reales y vuelve a cero al detener', async () => {
  const originalRaf = globalThis.requestAnimationFrame, originalCancel = globalThis.cancelAnimationFrame;
  let frame, disconnected = 0;
  globalThis.requestAnimationFrame = callback => { frame = callback; return 1; };
  globalThis.cancelAnimationFrame = () => { frame = null; };
  const levels = [];
  const node = () => ({ connect() {}, disconnect() { disconnected++; } });
  globalThis.AudioContext = class {
    constructor() { this.state = 'running'; }
    resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    createMediaElementSource() { return node(); }
    createAnalyser() { return { ...node(), fftSize: 512, getFloatTimeDomainData(samples) { samples.fill(0.125); } }; }
  };
  try {
    const player = new VoicePlayback(() => {}, level => levels.push(level));
    await player.start(new Blob(['test']));
    assert.equal(levels.at(-1), 0.5);
    player.setVolume(0); frame(); assert.equal(levels.at(-1), 0);
    const finished = player.finished; player.stop();
    assert.equal(await finished, 'stopped'); assert.equal(levels.at(-1), 0); assert.equal(disconnected, 2);
    assert.equal(frame, null);
  } finally { globalThis.requestAnimationFrame = originalRaf; globalThis.cancelAnimationFrame = originalCancel; }
});
