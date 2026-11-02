import { WakeWordGate } from './wake-word.js';
/** Turnos seriales: voz local -> texto local -> respuesta -> salida autorizada. */
export class ConversationLoop {
  constructor({ createCapture, transcribe, ask, speak, onState, onLevel, onText }) {
    Object.assign(this, { createCapture, transcribe, ask, speak, onState, onLevel, onText });
    this.enabled = false; this.revision = 0; this.state = 'off';
    this.wakeGate = new WakeWordGate(); this.requireWake = false;
  }
  idleState() { return this.requireWake && !this.wakeGate.open ? 'waiting_for_wake' : 'listening'; }
  setState(state) { this.state = state; this.onState(state); }
  // NOTA DEL OPERADOR: cada conexion tiene su revision. Una respuesta tardia
  // de un turno cancelado no puede hablar ni reactivar el microfono despues.
  current(generation) { return this.enabled && generation === this.revision; }
  async start(deviceId) {
    if (this.enabled) return;
    this.enabled = true;
    this.wakeGate.reset();
    const generation = ++this.revision;
    this.controller = new AbortController(); this.setState('loading');
    this.capture = this.createCapture({
      onLevel: value => { if (this.current(generation)) this.onLevel(value); },
      onSpeechStart: () => { if (this.current(generation)) this.setState('hearing'); },
      onSilence: () => { if (this.current(generation) && this.state === 'hearing') this.setState(this.idleState()); },
      onSpeechEnd: pcm => { void this.turn(pcm, generation); },
      onEnded: (state = 'device_lost') => { if (this.current(generation)) this.stop(state); },
    });
    const capture = this.capture;
    try {
      await capture.start(deviceId);
      if (!this.current(generation)) { capture.stop(); return; }
      this.setState(this.idleState());
    } catch (error) { if (this.current(generation)) this.stop(error.name === 'NotAllowedError' ? 'permission_required' : 'input_error'); }
  }
  async turn(pcm, generation) {
    if (!this.current(generation) || !['hearing', 'listening', 'waiting_for_wake'].includes(this.state)) { pcm.fill(0); return; }
    this.setState('transcribing');
    try {
      // Mientras T.H.O.R.N piensa y habla, cerramos la entrada. Asi no toma
      // su propia voz como una nueva orden al salir por los altavoces.
      await this.capture.pause();
      this.onLevel({ level: 0, seconds: 0 });
      if (!this.current(generation)) return;
      const signal = this.controller.signal;
      let value = await this.transcribe(pcm, signal);
      pcm.fill(0);
      if (!this.current(generation)) return;
      const gate = this.requireWake ? this.wakeGate.consume(value) : { accepted: true, text: value };
      value = gate.text;
      if (gate.accepted) this.onText(value);
      if (value.trim()) {
        this.setState('thinking');
        const reply = await this.ask(value, signal);
        if (!this.current(generation)) return;
        if (reply) { this.setState('speaking'); await this.speak(reply, signal); }
        if (this.requireWake) this.wakeGate.followUp();
      }
      if (!this.current(generation)) return;
      // Da tiempo a extinguirse la salida de altavoces antes de reabrir entrada.
      await new Promise(resolve => { this.cooldownResolve = resolve; this.cooldown = setTimeout(resolve, value.trim() ? 250 : 0); });
      this.cooldownResolve = null;
      if (!this.current(generation)) return;
      this.setState('loading'); await this.capture.start();
      if (this.current(generation)) {
        this.setState(this.idleState());
        clearTimeout(this.followTimer);
        if (this.requireWake && this.wakeGate.open) this.followTimer = setTimeout(() => {
          if (this.current(generation) && this.state === 'listening') this.setState(this.idleState());
        }, this.wakeGate.followUpMs + 5);
      }
    } catch { if (this.current(generation)) this.stop('turn_error'); }
    finally { pcm.fill(0); }
  }
  stop(state = 'off') {
    this.enabled = false; this.revision++;
    this.controller?.abort(); this.controller = null;
    clearTimeout(this.cooldown); this.cooldownResolve?.(); this.cooldownResolve = null;
    clearTimeout(this.followTimer); this.wakeGate.reset();
    this.capture?.stop(); this.capture = null;
    this.onLevel({ level: 0, seconds: 0 }); this.setState(state);
  }
}
