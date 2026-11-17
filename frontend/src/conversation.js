import { WakeWordGate } from './wake-word.js';
import { classifyInterruption } from './barge-in.js';

const CORRECTION = /^(?:no[, ]+)?quise decir(?:[,: ]+(.+))?[.!]?$/iu;

/** El STT entrega texto, o { text, wake } cuando el servidor confirmó el nombre por gramática. */
function heardFrom(result) {
  if (typeof result === 'string') return { text: result, wake: null };
  return { text: typeof result?.text === 'string' ? result.text : '',
    wake: typeof result?.wake === 'boolean' ? result.wake : null };
}

/** Turnos seriales: voz local -> texto local -> respuesta -> salida autorizada. */
export class ConversationLoop {
  constructor({ createCapture, transcribe, ask, speak, onState, onLevel, onText, onInterrupt, onTurnStart, onNoMatch,
    onDuck, onReplace, onWake, spokenText }) {
    Object.assign(this, { createCapture, transcribe, ask, speak, onState, onLevel, onText, onInterrupt, onTurnStart,
      onNoMatch, onDuck, onReplace, onWake, spokenText });
    this.enabled = false; this.revision = 0; this.state = 'off'; this.ducked = false;
    this.wakeGate = new WakeWordGate(); this.requireWake = false;
  }
  idleState() { return this.requireWake && !this.wakeGate.open ? 'waiting_for_wake' : 'listening'; }
  setState(state) { this.state = state; this.onState(state); }
  // NOTA DEL OPERADOR: cada conexion tiene su revision. Una respuesta tardia
  // de un turno cancelado no puede hablar ni reactivar el microfono despues.
  current(generation) { return this.enabled && generation === this.revision; }
  duck(active) {
    if (this.ducked === active) return;
    this.ducked = active; this.onDuck?.(active);
  }
  async start(deviceId) {
    if (this.enabled) return;
    this.enabled = true;
    this.wakeGate.reset();
    const generation = ++this.revision;
    this.controller = new AbortController(); this.setState('loading');
    this.deviceId = deviceId;
    this.capture = this.createCapture({
      onLevel: value => { if (this.current(generation)) this.onLevel(value); },
      onSpeechStart: () => {
        if (!this.current(generation)) return;
        // Bajar el volumen al primer indicio de voz: cortar no espera a la transcripcion.
        if (this.interruptOnly) this.duck(true); else this.setState('hearing');
      },
      onSilence: () => {
        if (!this.current(generation)) return;
        if (this.interruptOnly) { if (!this.interrupting) this.duck(false); }
        else if (this.state === 'hearing') this.setState(this.idleState());
      },
      onSpeechEnd: pcm => {
        if (!this.interruptOnly) { void this.turn(pcm, generation); return; }
        // La escucha de interrupcion sigue abierta mientras se transcribe este fragmento.
        this.capture?.rearm?.();
        void this.interrupt(pcm, generation);
      },
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
    const capturedAt = performance.now();
    const wake = this.requireWake && !this.wakeGate.open;
    this.setState('transcribing');
    try {
      // Mientras T.H.O.R.N piensa y habla, cerramos la entrada. Asi no toma
      // su propia voz como una nueva orden al salir por los altavoces.
      await this.capture.pause();
      this.onLevel({ level: 0, seconds: 0 });
      if (!this.current(generation)) return;
      const heard = heardFrom(await this.transcribe(pcm, this.controller.signal, { wake }));
      pcm.fill(0);
      if (!this.current(generation)) return;
      const gate = !this.requireWake ? { accepted: true, activated: false, text: heard.text }
        : heard.wake === true ? this.wakeGate.activate(heard.text)
        : heard.wake === false && !this.wakeGate.open ? { accepted: false, activated: false, text: '' }
        : this.wakeGate.consume(heard.text);
      let value = gate.text;
      const correction = CORRECTION.exec(value.trim());
      if (gate.accepted && value.trim()) {
        this.onTurnStart?.(capturedAt);
        if (correction?.[1]?.trim()) value = correction[1].trim();
      }
      if (gate.accepted) this.onText(value);
      if (gate.activated && !value.trim()) this.onWake?.();
      if (!this.requireWake && !value.trim()) this.onNoMatch?.();
      if (value.trim()) {
        const fixed = correction && !correction[1]?.trim() ? '¿Qué quisiste decir? Repite la frase completa.' : null;
        await this.respond(value, fixed, generation);
        if (!this.current(generation)) return;
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
  /**
   * Responde y, si el operador habla encima con una frase nueva, abandona esa
   * respuesta y atiende la nueva sin volver a pedir el nombre ni reabrir el micro.
   */
  async respond(value, fixed, generation) {
    this.setState('thinking');
    if (this.bargeIn) {
      this.interruptOnly = true;
      try { await this.capture.start(this.deviceId); }
      catch { this.interruptOnly = false; }
    }
    let prompt = value;
    try {
      while (prompt && this.current(generation)) {
        const turn = new AbortController();
        this.turnController = turn; this.pendingText = null;
        const signal = AbortSignal.any([this.controller.signal, turn.signal]);
        this.setState('thinking');
        let reply;
        try {
          reply = fixed ?? await this.ask(prompt, signal);
        } catch {
          // El HUD muestra el fallo. Reabrimos la escucha para que pueda reformular.
          if (!this.current(generation) || this.controller.signal.aborted) return;
        }
        fixed = null;
        if (!this.current(generation)) return;
        if (reply && !turn.signal.aborted) {
          this.setState('speaking');
          try { await this.speak(reply, signal); }
          catch (error) { if (!turn.signal.aborted) throw error; }
        }
        prompt = this.pendingText; this.pendingText = null;
        if (prompt && this.current(generation)) {
          this.duck(false);
          this.onTurnStart?.(performance.now());
          this.onText(prompt);
        }
      }
    } finally {
      this.turnController = null; this.pendingText = null;
      if (this.current(generation)) {
        this.duck(false);
        if (this.interruptOnly) await this.capture.pause();
      }
      this.interruptOnly = false;
    }
  }
  async interrupt(pcm, generation) {
    if (!this.current(generation) || !this.interruptOnly) { pcm.fill(0); return; }
    if (this.interrupting) {
      // Vosk atiende un clip a la vez: guardamos solo la frase mas reciente.
      this.queuedInterrupt?.fill(0); this.queuedInterrupt = pcm; return;
    }
    this.interrupting = true;
    let next = pcm;
    try {
      while (next) {
        const clip = next; next = null;
        let heard;
        try { heard = heardFrom(await this.transcribe(clip, this.controller.signal, {})).text.trim(); }
        finally { clip.fill(0); }
        if (!this.current(generation) || !this.interruptOnly) return;
        const kind = classifyInterruption(heard, this.spokenText?.() || '');
        if (kind === 'pause') { this.onInterrupt?.(); return; }
        if (kind === 'replace') {
          this.pendingText = heard; this.turnController?.abort(); this.onReplace?.(heard);
          return;
        }
        // Eco, asentimiento o ruido: la respuesta recupera su volumen y sigue.
        next = this.queuedInterrupt; this.queuedInterrupt = null;
        if (!next) this.duck(false);
      }
    } catch { if (this.current(generation)) this.duck(false); }
    finally {
      this.queuedInterrupt?.fill(0); this.queuedInterrupt = null;
      if (this.current(generation)) this.interrupting = false;
    }
  }
  stop(state = 'off') {
    this.enabled = false; this.revision++; this.interruptOnly = false; this.interrupting = false;
    this.turnController?.abort(); this.turnController = null; this.pendingText = null;
    this.queuedInterrupt?.fill(0); this.queuedInterrupt = null;
    this.controller?.abort(); this.controller = null;
    clearTimeout(this.cooldown); this.cooldownResolve?.(); this.cooldownResolve = null;
    clearTimeout(this.followTimer); this.wakeGate.reset();
    this.capture?.stop(); this.capture = null;
    this.duck(false);
    this.onLevel({ level: 0, seconds: 0 }); this.setState(state);
  }
}
