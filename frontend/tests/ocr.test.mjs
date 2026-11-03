import assert from 'node:assert/strict';
import test from 'node:test';
import { LocalOcr } from '../src/ocr.js';

test('OCR usa recursos locales y reutiliza un worker con salida de texto acotada', async () => {
  let created = 0, options, terminated = 0;
  const worker = { setParameters: async () => {}, recognize: async () => ({ data: { text: 'x'.repeat(4000), confidence: 90 } }), terminate: async () => { terminated++; } };
  const ocr = new LocalOcr({ factory: async (_lang, _mode, value) => { created++; options = value; return worker; }, terminate() {}, idleMs: 1000 });
  for (let i = 0; i < 2; i++) {
    const result = await ocr.recognize({ width: 100, height: 100 });
    assert.equal(result.text.length, 3000); assert.equal(result.truncated, true);
  }
  assert.equal(created, 1); assert.equal(options.cacheMethod, 'none');
  for (const name of ['workerPath', 'corePath', 'langPath']) assert(options[name].startsWith('/hud/assets'));
  ocr.stop(); assert.equal(terminated, 1);
});

test('cancelar durante inicialización termina inmediatamente el worker nativo', async () => {
  let initialized, killed = 0;
  const ocr = new LocalOcr({ factory: () => new Promise(resolve => { initialized = resolve; }), terminate: () => { killed++; } });
  const abort = new AbortController();
  const task = ocr.recognize({ width: 100, height: 100 }, { signal: abort.signal });
  abort.abort(); await assert.rejects(task, { name: 'AbortError' }); assert(killed > 0);
  let lateTerminated = false;
  initialized({ terminate: async () => { lateTerminated = true; } });
  await new Promise(resolve => setImmediate(resolve)); assert(lateTerminated);
});

test('OCR no acepta imágenes gigantes ni crea workers ilimitados', async () => {
  let resolve;
  const ocr = new LocalOcr({ factory: async () => ({ setParameters: async () => {}, recognize: () => new Promise(done => { resolve = done; }), terminate: async () => {} }), terminate() {} });
  await assert.rejects(ocr.recognize({ width: 5000, height: 5000 }), /ocr_image_limit/);
  const task = ocr.recognize({ width: 100, height: 100 }); await new Promise(done => setImmediate(done));
  await assert.rejects(ocr.recognize({ width: 100, height: 100 }), /ocr_busy/);
  resolve({ data: { text: 'lectura', confidence: 80 } }); await task; ocr.stop();
});
