/** Filtro de nombre sobre STT local: no envía frases ambientales al modelo. */

// NOTA DEL OPERADOR: esta lista debe coincidir con WAKE_ALIASES de core/commands.py
// (tests/test_wake_word.py lo comprueba). Son formas en que el STT escribe «Thorn».
export const WAKE_ALIASES = ['thorn', 'thorne', 'torn', 'thor', 'tor', 'fourth'];
// Solo al abrir una frase dirigida a Thorn: medidas con Vosk sobre «Thorn, …».
// No se usan para limpiar texto escrito, donde «Tom» puede ser un nombre real.
export const HEARD_AS_THORN = [...WAKE_ALIASES, 'tom', 'ton'];
const PREFIX = '(?:(?:oye|hola|hey|ok)\\s*[,!¡:]?\\s+)?';
const SPELLED = 't[. ]+h[. ]+o[. ]+r[. ]+n\\.?';
const NAME = new RegExp(`^${PREFIX}(?:${HEARD_AS_THORN.join('|')})\\b[\\s,.:;!?¿¡-]*`);
const ORIGINAL = new RegExp(`^${PREFIX}(?:${SPELLED}|${HEARD_AS_THORN.join('|')})\\b[\\s,.:;!?¿¡-]*`, 'i');

export function plain(value) {
  return value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/** Quita el nombre al inicio, conservando acentos y mayúsculas del resto. */
export function stripWakeWord(value) {
  const text = value.trim();
  const normalized = plain(text).replace(new RegExp(SPELLED, 'g'), 'thorn');
  if (!NAME.test(normalized)) return { matched: false, text };
  const prefix = text.match(ORIGINAL);
  return { matched: true, text: prefix ? text.slice(prefix[0].length).trim() : '' };
}

export class WakeWordGate {
  constructor({ clock = () => performance.now(), followUpMs = 30000 } = {}) {
    Object.assign(this, { clock, followUpMs }); this.until = -Infinity;
  }
  reset() { this.until = -Infinity; }
  get open() { return this.clock() < this.until; }
  followUp() { this.until = this.clock() + this.followUpMs; }
  /** El servidor ya confirmó el nombre con su gramática; el texto es lo dicho después. */
  activate(value) {
    this.followUp();
    return { accepted: true, activated: true, text: stripWakeWord(value).text };
  }
  /** Respaldo por alias cuando el servidor no puede detectar el nombre por sí mismo. */
  consume(value) {
    const result = stripWakeWord(value);
    if (result.matched) {
      this.followUp();
      return { accepted: true, activated: true, text: result.text };
    }
    return { accepted: this.open, activated: false, text: this.open ? result.text : '' };
  }
}
