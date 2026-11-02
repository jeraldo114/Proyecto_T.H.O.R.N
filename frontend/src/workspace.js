/** Resultados en RAM; el siguiente HUD puede suscribirse sin mezclar permisos y dibujo. */
export class WorkspaceClient {
  constructor(api, onChange = () => {}) { this.api = api; this.onChange = onChange; this.clear(); }
  clear() { this.generation = (this.generation || 0) + 1; this.revision = -1; this.localChanges = 0;
    this.pending = Promise.resolve(); this.panels = []; this.focused = ''; this.onChange(this.panels); }
  accept(panel) {
    if (!panel || !/^[a-f0-9]{32}$/.test(panel.id)) return;
    this.panels = [...this.panels.filter(p => p.id !== panel.id), panel].slice(-8);
    this.localChanges++;
    this.focused = panel.id; this.onChange(this.panels);
  }
  async refresh() {
    const generation = this.generation;
    const changes = this.localChanges;
    const value = await this.api('workspace');
    if (generation !== this.generation || changes !== this.localChanges || value.revision < this.revision) return;
    this.revision = value.revision; this.panels = value.panels; this.onChange(this.panels);
  }
  async change(id, operation, position) {
    const generation = this.generation;
    const work = this.pending.catch(() => {}).then(async () => {
      if (generation !== this.generation) return;
      const value = await this.api('workspace/change', { method: 'POST', body: { id, operation, ...(position ? { position } : {}) } });
      if (generation !== this.generation || value.revision < this.revision) return;
      this.revision = value.revision; this.localChanges++; this.panels = value.panels;
      if (operation === 'focus' || operation === 'restore') this.focused = id;
      if (!this.panels.some(p => p.id === this.focused)) this.focused = this.panels.at(-1)?.id || '';
      this.onChange(this.panels);
    });
    this.pending = work; return work;
  }
}

export function operatorIntent(prompt) {
  const value = prompt.trim().replace(/^[¿¡]\s*/u, '').replace(/[?!¡¿.]+$/u, '').trim()
    .replace(/^(?:thorn|t\.h\.o\.r\.n\.?)\s*[,;:]?\s+/iu, '');
  const map = /^(?:mu[eé]stra(?:me)?|abre|abrir|ver)(?:\s+un|\s+el)?\s+mapa\s+de\s+(.+)$/iu.exec(value);
  if (map) return { mode: 'map', value: map[1].trim() };
  return null;
}

export function localControl(prompt) {
  const value = prompt.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().replace(/[¿?¡!.,;:]/g, ' ').trim().replace(/\s+/g, ' ')
    .replace(/^(?:thorn|t h o r n)\s+/u, '').replace(/\s+(?:por favor|gracias)$/u, '');
  if (['silencio', 'detente', 'deten la respuesta', 'para de hablar', 'deja de hablar'].includes(value)) return 'stop';
  if (['borra el contexto', 'olvida esta conversacion', 'limpia la conversacion'].includes(value)) return 'clear';
  return null;
}
