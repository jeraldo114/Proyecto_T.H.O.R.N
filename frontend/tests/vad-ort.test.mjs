import test from 'node:test';
import assert from 'node:assert/strict';
import { preserveTensorInputs } from '../src/vad-ort-session.js';

test('el worker puede transferir cada entrada sin invalidar tensores reutilizados', async () => {
  class Tensor { constructor(type, data, dims) { Object.assign(this, { type, data, dims }); } }
  const feeds = { sr: new Tensor('int64', new BigInt64Array([16000n]), [1]),
    state: new Tensor('float32', new Float32Array([0.5, 0.25]), [1, 2]) };
  let calls = 0, released = false;
  const adapter = preserveTensorInputs({
    async run(copies) {
      calls++; assert.equal(copies.sr.data[0], 16000n);
      assert.notEqual(copies.state.data.buffer, feeds.state.data.buffer);
      structuredClone(copies.sr.data, { transfer: [copies.sr.data.buffer] });
      structuredClone(copies.state.data, { transfer: [copies.state.data.buffer] });
      return 'result';
    }, release() { released = true; },
  }, Tensor);
  assert.equal(await adapter.run(feeds), 'result'); assert.equal(await adapter.run(feeds), 'result');
  assert.equal(feeds.sr.data[0], 16000n); assert.equal(feeds.state.data[0], 0.5); assert.equal(calls, 2);
  adapter.release(); assert(released);
});
