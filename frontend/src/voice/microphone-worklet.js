// PCM mono int16 LE a 16 kHz. La conversion ocurre en el hilo de audio.
class ThornPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Float32Array(1600);
    this.used = 0;
    this.active = true;
    this.port.onmessage = event => {
      if (event.data?.type === 'flush') {
        this.active = false;
        this.flush();
        this.port.postMessage({ type: 'flushed' });
      }
    };
  }
  flush() {
    if (!this.used) return;
    const pcm = new ArrayBuffer(this.used * 2), view = new DataView(pcm);
    let energy = 0;
    for (let i = 0; i < this.used; i++) {
      const value = Math.max(-1, Math.min(1, this.samples[i]));
      energy += value * value;
      view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    this.port.postMessage({ type: 'pcm', pcm, level: Math.sqrt(energy / this.used) }, [pcm]);
    this.samples.fill(0);
    this.used = 0;
  }
  process(inputs) {
    if (!this.active) return false;
    const channel = inputs[0]?.[0];
    if (channel) for (const sample of channel) {
      this.samples[this.used++] = sample;
      if (this.used === this.samples.length) this.flush();
    }
    return true;
  }
}
registerProcessor('thorn-pcm-capture', ThornPcmCapture);
