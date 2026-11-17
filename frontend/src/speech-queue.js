/** Voz por frases: la primera suena antes y la siguiente se prepara mientras suena la actual. */

// Abreviaturas que terminan en punto sin cerrar la frase.
const ABBREVIATION = /\b(?:sr|sra|srta|dr|dra|ing|lic|etc|ej|p|pp|pag|aprox|num|vs|ud|uds|art|cap|fig)\.$/iu;
// Solo fin de oracion o salto de linea: cortar en «:» separaba «Memoria:» de «71 %».
const BOUNDARY = /[.!?…]+["»)\]]*\s+|\n+/gu;

/** Devuelve la primera frase completa y lo que queda, o null si aún falta texto. */
export function takeSentence(buffer, { minLength = 16, maxLength = 220 } = {}) {
  BOUNDARY.lastIndex = 0;
  for (let match; (match = BOUNDARY.exec(buffer));) {
    const end = match.index + match[0].length;
    const sentence = buffer.slice(0, end).trim();
    if (sentence.length < minLength) continue;
    if (ABBREVIATION.test(sentence.normalize('NFD').replace(/[̀-ͯ]/g, ''))) continue;
    return { sentence, rest: buffer.slice(end) };
  }
  // Una frase muy larga sin punto se corta en una coma para no demorar la voz.
  if (buffer.length > maxLength) {
    const comma = buffer.lastIndexOf(', ', maxLength);
    if (comma >= minLength) return { sentence: buffer.slice(0, comma + 1).trim(), rest: buffer.slice(comma + 2) };
  }
  return null;
}

export function splitSpeech(text, options) {
  const parts = [];
  let rest = text;
  for (let next; (next = takeSentence(rest, options)); rest = next.rest) parts.push(next.sentence);
  if (rest.trim()) parts.push(rest.trim());
  return parts;
}

const FINAL = new Set(['blocked', 'paused', 'stopped', 'playback_error']);

/**
 * Sintesis y reproduccion siguen en serie (Piper atiende una frase a la vez),
 * pero se solapan: mientras suena la frase N ya se sintetiza la N+1.
 */
export class SpeechQueue {
  constructor({ synthesize, play, signal }) {
    Object.assign(this, { synthesize, play, signal });
    this.synthesis = Promise.resolve();
    this.playback = Promise.resolve('idle');
    this.failure = null;
    this.texts = []; this.current = -1;
  }
  get stopped() { return Boolean(this.failure || this.signal?.aborted); }
  /** Frases que no llegaron a sonar; sirven para «sigue» tras una pausa o un bloqueo. */
  remaining() { return this.texts.slice(this.current + 1); }
  push(text) {
    const value = String(text || '').trim();
    if (!value || this.stopped) return false;
    const index = this.texts.push(value) - 1;
    const clip = this.synthesis.then(() => (this.stopped ? null : this.synthesize(value, this.signal)));
    // Un fallo detiene la cola: no se salta una frase en silencio.
    this.synthesis = clip.then(() => {}, error => { if (!this.signal?.aborted) this.failure ??= error; });
    this.playback = this.playback.then(async state => {
      if (this.stopped || FINAL.has(state)) return state;
      let blob;
      try { blob = await clip; } catch { return state; }
      if (!blob || this.stopped) return state;
      this.current = index;
      return this.play(blob);
    });
    return true;
  }
  /** Espera todo lo encolado hasta ahora; llamar después del último push. */
  async finished() {
    const state = await this.playback;
    if (FINAL.has(state) || this.signal?.aborted) return state;
    await this.synthesis;
    if (this.failure) throw this.failure;
    return state;
  }
}
