import { MicVAD } from '@ricky0123/vad-web/dist/real-time-vad.js';

export const MAX_SAMPLES = 16000 * 15;
export function toPcm16(audio) {
  const pcm = new Uint8Array(Math.min(audio.length, MAX_SAMPLES) * 2);
  const view = new DataView(pcm.buffer);
  for (let i = 0; i < pcm.length / 2; i++) {
    const sample = Math.max(-1, Math.min(1, audio[i]));
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return pcm;
}

/** Silero detecta finales de frase en esta PC. Todos sus archivos son locales. */
export class ConversationCapture {
  constructor(callbacks) { Object.assign(this, callbacks); this.closed = false; this.accepting = false; }
  async getStream() {
    if (this.closed) throw new Error('capture_closed');
    const stream = await navigator.mediaDevices.getUserMedia({ video: false, audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false,
      ...(this.deviceId ? { deviceId: { exact: this.deviceId } } : {}),
    } });
    if (this.closed) { stream.getTracks().forEach(track => track.stop()); throw new Error('capture_closed'); }
    this.stream = stream;
    stream.getAudioTracks().forEach(track => track.addEventListener('ended', () => {
      if (!this.closed && this.accepting) this.onEnded();
    }, { once: true }));
    return stream;
  }
  async start(deviceId) {
    if (this.closed) throw new Error('capture_closed');
    this.starting = true;
    if (deviceId !== undefined) this.deviceId = deviceId;
    try {
      if (!this.context) this.context = new AudioContext();
      // Un navegador puede exigir un primer gesto, incluso con permiso guardado.
      await Promise.race([this.context.resume(), new Promise((_, reject) => {
        this.resumeTimer = setTimeout(() => reject(new DOMException('Audio permission required', 'NotAllowedError')), 3000);
      })]).finally(() => clearTimeout(this.resumeTimer));
      if (this.closed) throw new Error('capture_closed');
      if (!this.vad) {
        this.vad = await MicVAD.new({
          model: 'v5', startOnLoad: false, processorType: 'AudioWorklet', audioContext: this.context,
          baseAssetPath: '/hud/assets/', onnxWASMBasePath: '/hud/assets/',
          ortConfig: ort => { ort.env.logLevel = 'error'; ort.env.wasm.numThreads = 1; ort.env.wasm.proxy = true; },
          positiveSpeechThreshold: 0.6, negativeSpeechThreshold: 0.4,
          redemptionMs: 650, preSpeechPadMs: 300, minSpeechMs: 250, submitUserSpeechOnPause: true,
          getStream: () => this.getStream(), resumeStream: () => this.getStream(),
          pauseStream: async stream => { stream.getTracks().forEach(track => track.stop()); this.stream = null; },
          onFrameProcessed: (_, frame) => {
            if (!this.accepting || this.closed) return;
            const level = Math.sqrt(frame.reduce((sum, sample) => sum + sample * sample, 0) / frame.length);
            if (this.speechSamples !== undefined) this.speechSamples += frame.length;
            this.onLevel({ level: Math.min(1, level * 4), seconds: (this.speechSamples || 0) / 16000 });
            if ((this.speechSamples || 0) >= MAX_SAMPLES - 8192) void this.pause();
          },
          onSpeechStart: () => {
            if (!this.accepting || this.closed) return;
            this.speechSamples = 0; this.onSpeechStart();
            this.limitTimer = setTimeout(() => { if (this.accepting && !this.closed) void this.pause(); }, 14000);
          },
          onVADMisfire: () => { clearTimeout(this.limitTimer); this.speechSamples = undefined; if (this.accepting && !this.closed) this.onSilence?.(); },
          onSpeechEnd: audio => {
            clearTimeout(this.limitTimer); this.speechSamples = undefined;
            if (this.closed || !this.accepting) { audio.fill(0); return; }
            this.accepting = false;
            const pcm = toPcm16(audio); audio.fill(0); this.onSpeechEnd(pcm);
          },
        });
      }
      if (this.closed) throw new Error('capture_closed');
      this.accepting = true;
      await this.vad.start();
      if (this.closed) throw new Error('capture_closed');
      // Chrome procesa el worklet si su grafo llega a salida; ganancia cero
      // mantiene la entrada totalmente inaudible y evita realimentacion.
      if (!this.silence) {
        this.silence = this.context.createGain(); this.silence.gain.value = 0;
        const node = this.vad.getAudioInstances().vadNode;
        node.connect(this.silence);
        this.silence.connect(this.context.destination);
        this.frames = [];
        const process = node.port.onmessage;
        node.port.onmessage = event => {
          if (this.closed || !this.accepting || this.pausing) { this.clearFrame(event); return; }
          if (this.frames.length >= 8) { this.clearFrame(event); this.onEnded('input_error'); this.stop(); return; }
          this.frames.push(event);
          if (!this.processing) this.processing = this.drainFrames(process).finally(() => { this.processing = null; });
        };
      }
    } catch (error) { this.stop(); throw error; }
    finally { this.starting = false; if (this.closed) this.disposeVad(); }
  }
  async pause() {
    if (this.pausePromise) return this.pausePromise;
    clearTimeout(this.limitTimer);
    this.pausing = true;
    // Cierre inmediato: no depender de que termine una inferencia para parar tracks.
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    this.clearFrames();
    this.pausePromise = (async () => {
      // Termina el frame en vuelo antes de resetear el estado recurrente.
      await this.processing;
      await this.vad?.pause();
      this.accepting = false;
    })().finally(() => { this.pausing = false; this.pausePromise = null; });
    return this.pausePromise;
  }
  clearFrame(event) {
    const buffer = event.data?.data;
    if (buffer instanceof ArrayBuffer && buffer.byteLength) new Uint8Array(buffer).fill(0);
  }
  clearFrames() { this.frames?.forEach(event => this.clearFrame(event)); this.frames = []; }
  async drainFrames(process) {
    // Silero es recurrente: una inferencia a la vez, backlog maximo 256 ms.
    while (!this.closed && this.accepting && !this.pausing && this.frames.length) {
      let deadline;
      try {
        await Promise.race([process(this.frames.shift()), new Promise((_, reject) => {
          deadline = setTimeout(() => reject(new Error('vad_timeout')), 4000);
        })]);
      } catch { if (!this.closed) { this.onEnded('input_error'); this.stop(); } }
      finally { clearTimeout(deadline); }
    }
  }
  stop() {
    this.closed = true; this.accepting = false;
    this.clearFrames();
    this.silence?.disconnect(); this.silence = null;
    clearTimeout(this.limitTimer); clearTimeout(this.resumeTimer);
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    void this.context?.close().catch(() => {});
    // new/start pendientes se limpian en su catch, sin aceptar permisos tardios.
    if (!this.starting) this.disposeVad();
  }
  disposeVad() {
    if (this.vad) {
      const vad = this.vad; this.vad = null;
      if (vad.initializationState === 'initialized') void vad.destroy().catch(() => {});
      else void vad.model.release().catch(() => {});
    }
  }
}
