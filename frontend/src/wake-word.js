/** Filtro de nombre sobre STT local: no envía frases ambientales al modelo. */
export class WakeWordGate {
  constructor({ clock = () => performance.now(), followUpMs = 30000 } = {}) {
    Object.assign(this, { clock, followUpMs }); this.until = -Infinity;
  }
  reset() { this.until = -Infinity; }
  get open() { return this.clock() < this.until; }
  followUp() { this.until = this.clock() + this.followUpMs; }
  consume(value) {
    const text = value.trim();
    const normalized = text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/t[. ]+h[. ]+o[. ]+r[. ]+n\.?/g, 'thorn');
    // Vosk español suele escribir «Tor» o «Torn». Solo al principio, nunca
    // por encontrar esas letras dentro de otra palabra o de una cita larga.
    const match = normalized.match(/^(?:(?:oye|hola|hey|ok)\s*[,!¡:]?\s+)?(?:thorn|torn|thor|tor)\b[\s,.:;!?¿¡-]*/);
    if (match) {
      this.followUp();
      const originalPrefix = text.match(/^(?:(?:oye|hola|hey|ok)\s*[,!¡:]?\s+)?(?:t[. ]+h[. ]+o[. ]+r[. ]+n\.?|thorn|torn|thor|tor)\b[\s,.:;!?¿¡-]*/i);
      return { accepted: true, activated: true, text: originalPrefix ? text.slice(originalPrefix[0].length).trim() : '' };
    }
    return { accepted: this.open, activated: false, text: this.open ? text : '' };
  }
}
