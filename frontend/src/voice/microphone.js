export class PcmMicrophone {
  constructor({ onLevel, onLimit, onEnded, maxSeconds = 15 }) {
    this.onLevel = onLevel; this.onLimit = onLimit; this.onEnded = onEnded;
    this.maxBytes = 16000 * Math.min(15, maxSeconds) * 2;
    this.chunks = []; this.bytes = 0; this.closed = false; this.finishing = false;
  }
  static supported() {
    return Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.AudioContext && window.AudioWorkletNode);
  }
  async start(deviceId) {
    if (!PcmMicrophone.supported()) throw new Error('microphone_unsupported');
    const stream = await navigator.mediaDevices.getUserMedia({ video: false, audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false,
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    } });
    if (this.closed) { stream.getTracks().forEach(track => track.stop()); throw new Error('microphone_cancelled'); }
    this.stream = stream;
    stream.getAudioTracks().forEach(track => track.addEventListener('ended', () => {
      if (!this.closed && !this.finishing) this.onEnded();
    }, { once: true }));
    try {
      this.context = new AudioContext({ sampleRate: 16000 });
      if (this.context.sampleRate !== 16000) throw new Error('microphone_sample_rate');
      await this.context.audioWorklet.addModule('/hud/assets/microphone-worklet.js');
      if (this.closed) throw new Error('microphone_cancelled');
      await this.context.resume();
      if (this.closed) throw new Error('microphone_cancelled');
      this.node = new AudioWorkletNode(this.context, 'thorn-pcm-capture');
      this.node.port.onmessage = event => {
        if (this.closed) return;
        if (event.data?.type === 'flushed') { this.flushResolve?.(); return; }
        const pcm = event.data?.pcm;
        if (event.data?.type !== 'pcm' || !(pcm instanceof ArrayBuffer) || pcm.byteLength % 2) return;
        const remaining = this.maxBytes - this.bytes;
        const chunk = new Uint8Array(pcm, 0, Math.min(remaining, pcm.byteLength));
        if (chunk.length) { this.chunks.push(chunk); this.bytes += chunk.length; }
        this.onLevel({ level: Math.max(0, Math.min(1, event.data.level || 0)), seconds: this.bytes / 32000 });
        if (this.bytes >= this.maxBytes && !this.finishing) this.onLimit();
      };
      this.source = this.context.createMediaStreamSource(stream);
      this.silence = this.context.createGain(); this.silence.gain.value = 0;
      this.source.connect(this.node); this.node.connect(this.silence); this.silence.connect(this.context.destination);
      this.timer = setTimeout(() => { if (!this.closed && !this.finishing) this.onLimit(); }, this.maxBytes / 32);
    } catch (error) { this.cancel(); throw error; }
  }
  async finish() {
    if (this.closed || this.finishing || !this.node) throw new Error('microphone_cancelled');
    this.finishing = true; clearTimeout(this.timer);
    this.stream?.getTracks().forEach(track => track.stop());
    try {
      await new Promise((resolve, reject) => {
        this.flushResolve = resolve; this.flushReject = reject;
        this.flushTimer = setTimeout(() => reject(new Error('microphone_capture_failed')), 1500);
        this.node.port.postMessage({ type: 'flush' });
      });
      if (this.closed) throw new Error('microphone_cancelled');
      const pcm = new Uint8Array(this.bytes);
      let offset = 0;
      for (const chunk of this.chunks) { pcm.set(chunk, offset); offset += chunk.length; }
      if (pcm.length < 3200) throw new Error('microphone_too_short');
      return pcm;
    } finally { this.cancel(); }
  }
  cancel() {
    this.closed = true; clearTimeout(this.timer); clearTimeout(this.flushTimer);
    this.flushReject?.(new Error('microphone_cancelled'));
    this.flushResolve = null; this.flushReject = null;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    this.source?.disconnect(); this.node?.disconnect(); this.silence?.disconnect();
    if (this.node) { this.node.port.onmessage = null; this.node.port.close(); }
    this.context?.close().catch(() => {});
    this.source = null; this.node = null; this.silence = null;
    this.chunks.forEach(chunk => chunk.fill(0)); this.chunks = []; this.bytes = 0;
  }
}
