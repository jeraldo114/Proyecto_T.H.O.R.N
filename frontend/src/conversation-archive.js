/** Cuaderno cifrado del operador. El navegador conserva solo una página en RAM. */
export class ConversationArchive {
  constructor({ host, api, active, onError, cancel }) {
    Object.assign(this, { api, active, onError, cancel });
    this.revision = 0; this.before = null; this.query = ''; this.loading = false;
    const section = document.createElement('section'); section.className = 'conversation-archive';
    section.innerHTML = `<div class="section-title"><h2>CONVERSACIONES / MEMORIA PERMANENTE</h2><button type="button" class="command-button" data-refresh>Actualizar</button></div>
      <p data-status role="status">Conecta una sesión para consultar tus conversaciones.</p>
      <p>Los turnos completados se guardan cifrados en esta PC. Al reconectar, T.H.O.R.N recupera recuerdos recientes y relacionados. La búsqueda encuentra palabras; no es una memoria infalible.</p>
      <form data-search><label>Buscar conversaciones anteriores<input maxlength="200" name="query" placeholder="Ejemplo: proyecto de electrónica"></label><button class="command-button">Buscar</button></form>
      <div data-records></div><button type="button" class="command-button" data-older hidden>Conversaciones anteriores</button>
      <details><summary>Olvidar el historial de conversaciones</summary><p>Borra este archivo y el contexto en RAM. Conserva las notas explícitas y los respaldos ya creados.</p>
      <form data-forget><label>Escribe BORRAR MEMORIA<input name="confirmation" autocomplete="off" required></label><button class="command-button">Borrar conversaciones permanentemente</button></form></details>`;
    host.prepend(section); this.section = section;
    this.status = section.querySelector('[data-status]'); this.records = section.querySelector('[data-records]');
    section.querySelector('[data-refresh]').onclick = () => void this.load();
    section.querySelector('[data-older]').onclick = () => void this.load(this.before);
    section.querySelector('[data-search]').onsubmit = event => { event.preventDefault(); this.query = event.target.elements.query.value.trim(); void this.load(); };
    section.querySelector('[data-forget]').onsubmit = async event => {
      event.preventDefault();
      if (!this.active()) return;
      const revision = ++this.revision;
      try {
        await this.cancel();
        await this.api('conversation/forget', { method: 'POST', body: { confirmation: event.target.elements.confirmation.value } });
        if (revision !== this.revision || !this.active()) return;
        event.target.reset(); this.records.replaceChildren(); this.status.textContent = 'Conversaciones borradas. Las notas explícitas se conservan.';
        this.section.querySelector('[data-older]').hidden = true;
      } catch (error) { if (revision === this.revision) this.onError(error); }
    };
  }
  async load(before = null) {
    if (!this.active() || this.loading) return;
    const revision = ++this.revision; this.loading = true;
    this.status.textContent = 'Leyendo el archivo cifrado local…';
    try {
      // La búsqueda va en JSON: sus palabras no quedan en URLs o access logs.
      const result = await this.api('conversation/history', { method: 'POST',
        body: { query: this.query, ...(before ? { before } : {}) } });
      if (revision !== this.revision || !this.active()) return;
      this.records.replaceChildren(...result.records.map(turn => {
        const article = document.createElement('article'); article.className = 'conversation-turn';
        const date = document.createElement('small'); date.textContent = `${new Date(turn.created_at).toLocaleString('es-CL')} · ${turn.source}`;
        const question = document.createElement('p'); question.textContent = `Tú: ${turn.prompt}`;
        const answer = document.createElement('p'); answer.textContent = `T.H.O.R.N: ${turn.reply}`;
        article.append(date, question, answer); return article;
      }));
      this.before = result.next_before;
      this.section.querySelector('[data-older]').hidden = !this.before;
      this.status.textContent = result.records.length ? 'Archivo local cifrado · Se conserva al desconectar.' : 'No hay conversaciones para esta búsqueda.';
    } catch (error) {
      if (revision === this.revision) { this.status.textContent = 'Memoria permanente no disponible: revisa MySQL y el cifrado.'; this.onError(error); }
    } finally { if (revision === this.revision) this.loading = false; }
  }
  render(memory, connected) {
    this.section.querySelectorAll('button,input').forEach(node => { node.disabled = !connected || !memory?.available; });
    if (!connected) return;
    if (!memory?.available) this.status.textContent = `Memoria permanente: ${memory?.state || 'sin lectura'}. Los turnos actuales no se están archivando.`;
  }
  note(result) {
    if (!result.memory) return;
    this.status.textContent = result.memory.saved ? 'Último turno guardado y cifrado.' : 'Este turno no se guardó en la memoria permanente. Revisa MySQL, cifrado y permisos.';
  }
  disconnect() {
    this.revision++; this.loading = false; this.before = null; this.query = '';
    this.records.replaceChildren(); this.section.querySelectorAll('form').forEach(form => form.reset());
    this.status.textContent = 'Sesión desconectada. El archivo cifrado permanece en esta PC.';
    this.section.querySelector('[data-older]').hidden = true;
    this.render(null, false);
  }
}
