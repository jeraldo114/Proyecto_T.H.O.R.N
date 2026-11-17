// Salida de audio: conserva el WAV/MP3 bloqueado sin solicitar otra sintesis.
// Volumen relativo mientras el operador habla encima de la respuesta.
export const DUCK_LEVEL = 0.15;
export class VoicePlayback {
  constructor(onState, onLevel = () => {}) {
    this.onState = onState;
    this.onLevel = onLevel;
    this.finished = Promise.resolve('idle');
    this.audio = null;
    this.url = null;
    this.context = null;
    this.oscillator = null;
    this.revision = 0;
    this.volume = 1;
    this.state = 'idle';
  }

  get active() { return Boolean(this.audio || this.context); }

  notify(state) {
    this.state = state;
    this.onState(state);
  }

  setVolume(value) {
    if (!Number.isFinite(value) || value < 0 || value > 1) return;
    this.volume = value;
    this.applyVolume();
  }

  /** Baja la voz mientras el operador empieza a hablar; no pausa ni descarta el clip. */
  duck(active) {
    this.ducked = Boolean(active);
    this.applyVolume();
  }

  applyVolume() {
    if (this.audio) this.audio.volume = this.volume * (this.ducked ? DUCK_LEVEL : 1);
  }

  stop() {
    this.revision++;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.source?.disconnect(); this.analyser?.disconnect();
    this.source = null; this.analyser = null;
    this.onLevel(0);
    this.finishResolve?.('stopped'); this.finishResolve = null;
    if (this.audio) {
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      this.audio = null;
    }
    if (this.url) { URL.revokeObjectURL(this.url); this.url = null; }
    if (this.oscillator) {
      this.oscillator.onended = null;
      try { this.oscillator.stop(); } catch { /* Puede no haber iniciado aun. */ }
      this.oscillator.disconnect();
      this.oscillator = null;
    }
    if (this.context) {
      void this.context.close().catch(() => {});
      this.context = null;
    }
    this.notify('idle');
  }

  async start(blob) {
    this.stop();
    const revision = this.revision;
    this.finished = new Promise(resolve => { this.finishResolve = resolve; });
    this.url = URL.createObjectURL(blob);
    this.audio = new Audio(this.url);
    this.applyVolume();
    this.audio.addEventListener('ended', () => {
      if (revision !== this.revision) return;
      this.finishResolve?.('ended'); this.finishResolve = null;
      this.stop(); this.notify('ended');
    }, { once: true });
    this.audio.addEventListener('error', () => {
      if (revision !== this.revision) return;
      this.finishResolve?.('playback_error'); this.finishResolve = null;
      this.stop(); this.notify('playback_error');
    }, { once: true });
    // El analizador comparte la salida audible; no crea un segundo reproductor.
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (Context) {
      this.context = new Context();
      this.source = this.context.createMediaElementSource(this.audio);
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 512;
      this.source.connect(this.analyser); this.analyser.connect(this.context.destination);
    }
    await this.play(revision);
  }

  pause() {
    if (this.state !== 'playing' || !this.audio) return false;
    this.audio.pause();
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.onLevel(0);
    this.finishResolve?.('paused'); this.finishResolve = null;
    this.notify('paused');
    return true;
  }

  async resume() {
    if (!this.audio || !['blocked', 'paused'].includes(this.state)) return;
    if (this.state === 'paused') {
      this.finished = new Promise(resolve => { this.finishResolve = resolve; });
    }
    await this.play(this.revision);
  }

  async play(revision) {
    const audio = this.audio;
    this.notify('starting');
    try {
      if (this.context) {
        await Promise.race([this.context.resume(), new Promise((_, reject) => {
          this.resumeTimer = setTimeout(() => reject(new DOMException('Audio permission required', 'NotAllowedError')), 3000);
        })]).finally(() => clearTimeout(this.resumeTimer));
        if (revision !== this.revision || audio !== this.audio) return;
        if (this.context.state !== 'running') throw new DOMException('Audio permission required', 'NotAllowedError');
      }
      await audio.play();
      if (revision === this.revision && audio === this.audio) {
        this.notify('playing');
        const samples = new Float32Array(this.analyser?.fftSize || 0);
        const measure = () => {
          if (revision !== this.revision) return;
          if (this.analyser) {
            this.analyser.getFloatTimeDomainData(samples);
            const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
            this.onLevel(audio.paused || audio.muted ? 0 : Math.min(1, rms * this.volume * 4));
            this.frame = requestAnimationFrame(measure);
          }
        };
        measure();
      }
    } catch (error) {
      if (revision !== this.revision || audio !== this.audio) return;
      // NOTA DEL OPERADOR: si el navegador pide un gesto, conservamos el audio.
      // Reanudar reproduce ese mismo clip; no vuelve a sintetizarlo ni cobrarlo.
      if (error.name === 'NotAllowedError') this.notify('blocked');
      else { this.finishResolve?.('playback_error'); this.finishResolve = null; this.stop(); this.notify('playback_error'); }
    }
  }

  /** Tono breve de «te escucho» al detectar el nombre. No usa el clip ni su analizador. */
  async cue() {
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!Context || this.volume === 0) return false;
    let context;
    try {
      context = new Context();
      await context.resume();
      const gain = context.createGain();
      gain.gain.setValueAtTime(0.0001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(this.volume * 0.08, context.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.26);
      gain.connect(context.destination);
      for (const [frequency, offset] of [[660, 0], [880, 0.09]]) {
        const tone = context.createOscillator();
        tone.frequency.value = frequency; tone.connect(gain);
        tone.start(context.currentTime + offset); tone.stop(context.currentTime + offset + 0.16);
      }
      await new Promise(resolve => setTimeout(resolve, 300));
      return true;
    } catch { return false; }
    finally { void context?.close().catch(() => {}); }
  }

  async testOutput() {
    this.stop();
    const revision = this.revision;
    try {
      const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
      const context = new Context();
      this.context = context;
      this.notify('starting');
      await context.resume();
      if (revision !== this.revision) return;
      const gain = context.createGain();
      gain.gain.value = this.volume * 0.12;
      gain.connect(context.destination);
      const oscillator = context.createOscillator();
      this.oscillator = oscillator;
      oscillator.frequency.value = 440;
      oscillator.connect(gain);
      oscillator.onended = () => {
        if (revision === this.revision) { this.stop(); this.notify('test_finished'); }
      };
      oscillator.start();
      oscillator.stop(context.currentTime + 0.4);
      this.notify('testing');
    } catch (error) {
      if (revision !== this.revision) return;
      this.stop();
      this.notify(error.name === 'NotAllowedError' ? 'test_blocked' : 'playback_error');
    }
  }
}
