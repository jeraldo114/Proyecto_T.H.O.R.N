/** Carga diferida: el dictado manual no descarga ni abre el detector neuronal. */
export class LazyConversationCapture {
  constructor(callbacks) { this.callbacks = callbacks; this.closed = false; }
  async start(deviceId) {
    if (!this.capture) {
      const { ConversationCapture } = await import('/hud/assets/conversation-vad.js');
      if (this.closed) throw new Error('capture_closed');
      this.capture = new ConversationCapture(this.callbacks);
    }
    if (this.closed) throw new Error('capture_closed');
    await this.capture.start(deviceId);
    if (this.closed) this.capture.stop();
  }
  async pause() { await this.capture?.pause(); }
  stop() { this.closed = true; this.capture?.stop(); }
}
