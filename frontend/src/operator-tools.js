import { LocalCamera } from './camera.js';
import { WorkspaceClient } from './workspace.js';
import { ConversationArchive } from './conversation-archive.js';

/** Herramientas reales: el escritorio puede mover sus controles sin duplicarlos. */
export class OperatorTools {
  constructor({ api, credential, active, onReply, onError, navigate, cancelResponse, onSources,
    canAnnounce = () => true, canPresent = () => true }) {
    Object.assign(this, { api, credential, active, onReply, onError, navigate, cancelResponse, onSources, canAnnounce, canPresent });
    this.generation = 0; this.connected = false; this.document = ''; this.dragPosition = null;
    this.api = async (...args) => {
      const generation = this.generation;
      const value = await api(...args);
      if (generation !== this.generation || !this.active()) throw new Error('session_changed');
      return value;
    };
    const section = document.createElement('section'); section.className = 'b9-tools';
    // Solo esta plantilla fija usa HTML. Los nombres, resultados y documentos usan textContent/value.
    section.innerHTML = `<div class="section-title"><h2>BASTION 9 / HERRAMIENTAS</h2><span id="b9-state">SIN SESIÓN</span></div>
      <div class="b9-controls"><button class="command-button" id="b9-brief">Resumen de inicio</button><button class="command-button" id="b9-clear">Borrar contexto del chat</button><button class="command-button" id="b9-cancel">Detener respuesta</button></div>
      <h3>Documentos PDF locales</h3><form id="b9-document-form"><label for="b9-file">Elegir PDF (hasta 8 MiB / 40 páginas)</label><input id="b9-file" type="file" accept="application/pdf,.pdf" required><button class="command-button" type="submit">Leer documento</button></form>
      <form id="b9-document-question"><label for="b9-doc-select">Documento de esta sesión</label><select id="b9-doc-select"><option value="">Sin documento</option></select><label for="b9-question">Consulta local sobre el texto extraído</label><input id="b9-question" maxlength="2000" required><button class="command-button" type="submit">Consultar PDF</button><button class="command-button" type="button" id="b9-download">Descargar original</button></form><pre id="b9-document-text" class="b9-document-text"></pre>
      <h3>Mapas con fuente</h3><form id="b9-map-form"><label for="b9-place">Ciudad o lugar</label><input id="b9-place" maxlength="120" required><button class="command-button" type="submit">Buscar coordenadas y mapa</button></form><p>Usa la autorización de Internet de «Red e Internet». Las coordenadas provienen de artículos de Wikipedia; no se lee tu GPS.</p>
      <h3>Cámara y gestos locales</h3><label><input id="b9-camera-consent" type="checkbox"> Autorizo la cámara local durante esta sesión. Las imágenes no salen del navegador.</label><div class="b9-controls"><button class="command-button" id="b9-camera-start">Iniciar cámara</button><button class="command-button" id="b9-camera-stop">Apagar cámara</button><label><input type="checkbox" id="b9-gestures"> Seguimiento de manos (experimental)</label></div><video id="b9-video" muted playsinline hidden></video><p id="b9-vision-state" role="status">CÁMARA APAGADA</p><p id="b9-gesture-state" role="status"></p><p>Palma abierta durante 0,6 s para activar el cursor. Selecciona un resultado abajo y usa una pinza para actualizar su posición. Los gestos solo controlan resultados del HUD.</p>
      <h3>Resultados de esta sesión</h3><div id="b9-panels"></div>
      <h3>Preferencias del operador</h3><form id="b9-preferences"><label for="b9-name">Cómo llamarte</label><input id="b9-name" maxlength="60"><label for="b9-zone">Zona horaria IANA</label><input id="b9-zone" value="America/Santiago" maxlength="80" required><label><input id="b9-welcome" type="checkbox" checked> Mostrar resumen al conectar</label><label><input id="b9-memory" type="checkbox" checked> Usar notas y conversaciones archivadas como contexto</label><button class="command-button" type="submit" id="b9-save-preferences">Guardar preferencias cifradas</button></form><p id="b9-operation" role="status"></p>`;
    document.querySelector('#view-assistant').append(section);
    this.section = section;
    this.toolGroups = new Map();
    for (const [heading, name] of [['Documentos PDF locales', 'documents'], ['Mapas con fuente', 'maps'],
      ['Cámara y gestos locales', 'camera'], ['Preferencias del operador', 'preferences']]) {
      const start = [...section.querySelectorAll('h3')].find(node => node.textContent === heading);
      const group = document.createElement('div'); group.className = 'tool-group'; group.dataset.toolGroup = name;
      section.insertBefore(group, start);
      let node = start;
      while (node && (node === start || node.tagName !== 'H3')) { const next = node.nextElementSibling; group.append(node); node = next; }
      this.toolGroups.set(name, group);
    }
    this.nodes = new Map([...section.querySelectorAll('[id]')].map(node => [`#${node.id}`, node]));
    this.controls = [...section.querySelectorAll('button,input,select')];
    this.$ = selector => this.nodes.get(selector) || section.querySelector(selector);
    this.workspace = new WorkspaceClient((...args) => this.api(...args), panels => this.renderPanels(panels));
    this.archive = new ConversationArchive({ host: document.querySelector('#view-history'), api: this.api,
      active: this.active, onError: this.onError, cancel: cancelResponse });
    this.camera = new LocalCamera(this.$('#b9-video'), {
      onState: state => {
        const names = { off: 'CÁMARA APAGADA', requesting: 'ESPERANDO PERMISO', preview: 'VISTA LOCAL', tracking: 'SEGUIMIENTO LOCAL ACTIVADO', loading_model: 'CARGANDO DETECTOR LOCAL', permission_required: 'PERMISO DE CÁMARA REQUERIDO', device_lost: 'CÁMARA DESCONECTADA', camera_error: 'NO SE PUDO ABRIR LA CÁMARA', vision_error: 'SEGUIMIENTO NO DISPONIBLE', vision_timeout: 'EL DETECTOR NO RESPONDIÓ', vision_model_unavailable: 'DETECTOR DE MANOS NO DISPONIBLE', vision_inference_failed: 'NO SE PUDO PROCESAR LA IMAGEN' };
        this.$('#b9-vision-state').textContent = names[state] || `VISIÓN: ${state}`;
        this.$('#b9-video').hidden = !this.camera.stream;
        if (state !== 'tracking' && state !== 'loading_model') this.$('#b9-gestures').checked = false;
        window.dispatchEvent(new CustomEvent('thorn-camera-state', { detail: { state, active: Boolean(this.camera.stream) } }));
      },
      onGesture: result => this.gesture(result),
    });
    this.$('#b9-brief').onclick = () => this.perform(async () => this.accept(await this.api('briefing')));
    this.$('#b9-cancel').onclick = () => cancelResponse();
    this.$('#b9-clear').onclick = () => this.perform(async () => {
      await cancelResponse(); await this.api('conversation/reset', { method: 'POST', body: {} });
      this.accept({ reply: 'Contexto de esta conversación borrado.', ui_commands: [{ type: 'clear_context' }] }, { announce: false });
      this.$('#b9-operation').textContent = 'Contexto y resultados de esta sesión borrados. Las conversaciones archivadas y las notas se conservan.';
    });
    this.$('#b9-document-form').onsubmit = event => {
      event.preventDefault(); void this.perform(() => this.upload());
    };
    this.$('#b9-doc-select').onchange = event => { this.document = event.target.value; this.showDocument(); };
    this.$('#b9-document-question').onsubmit = event => {
      event.preventDefault(); void this.perform(async () => {
        if (!this.document) throw new Error('document_not_found');
        this.accept(await this.api('documents/ask', { method: 'POST', body: { id: this.document, prompt: this.$('#b9-question').value } }));
      });
    };
    this.$('#b9-download').onclick = () => this.perform(() => this.downloadDocument());
    this.$('#b9-map-form').onsubmit = event => {
      event.preventDefault(); void this.perform(async () => this.accept(await this.api('maps/lookup', {
        method: 'POST', body: { query: this.$('#b9-place').value, internet_consent: document.querySelector('#internet-consent').checked },
      })));
    };
    this.$('#b9-camera-start').onclick = () => this.perform(async () => {
      if (!this.capabilities?.camera_allowed) throw new Error('insufficient_scope');
      await this.camera.start({ consent: this.$('#b9-camera-consent').checked });
    });
    this.$('#b9-camera-stop').onclick = () => this.camera.stop();
    this.$('#b9-camera-consent').onchange = event => { if (!event.target.checked) this.camera.stop(); };
    this.$('#b9-gestures').onchange = event => {
      if (event.target.checked) void this.perform(async () => {
        if (!this.camera.stream) { event.target.checked = false; throw new Error('camera_not_started'); }
        await this.camera.enableGestures();
      }); else this.camera.stopGestures();
    };
    this.$('#b9-preferences').onsubmit = event => {
      event.preventDefault(); void this.perform(async () => {
        await this.api('preferences/save', { method: 'POST', body: { preferences: {
          display_name: this.$('#b9-name').value, timezone: this.$('#b9-zone').value,
          briefing_enabled: this.$('#b9-welcome').checked, memory_context_enabled: this.$('#b9-memory').checked,
        } } });
        this.$('#b9-operation').textContent = 'Preferencias guardadas.';
      });
    };
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.camera.stop(); });
    window.addEventListener('pagehide', () => this.camera.stop());
    window.addEventListener('thorn-document-ocr', event => {
      if (!this.active()) return;
      if (event.detail.panel) this.workspace.accept(event.detail.panel);
      this.showDocument();
    });
  }
  async perform(work) {
    if (!this.active()) { this.onError(new Error('unauthorized')); return; }
    const generation = this.generation;
    try { await work(); }
    catch (error) { if (generation === this.generation) this.onError(error); }
  }
  async connect() {
    if (this.connected) return;
    this.connected = true; const generation = this.generation;
    const results = await Promise.allSettled([this.api('preferences'), this.workspace.refresh()]);
    if (generation !== this.generation || !this.active()) return;
    if (results[0].status === 'fulfilled') {
      const { preferences: p, durable } = results[0].value;
      this.$('#b9-name').value = p.display_name; this.$('#b9-zone').value = p.timezone;
      this.$('#b9-welcome').checked = p.briefing_enabled; this.$('#b9-memory').checked = p.memory_context_enabled;
      this.$('#b9-save-preferences').disabled = !durable;
      if (p.briefing_enabled) {
        try {
          const result = await this.api('briefing');
          if (this.canPresent()) this.accept(result, { announce: this.canAnnounce() });
          else if (result.panel) this.workspace.accept(result.panel);
        }
        catch (error) { if (generation === this.generation) this.onError(error); }
      }
    }
  }
  render(capabilities) {
    this.capabilities = capabilities?.operator;
    const enabled = this.active();
    this.controls.forEach(node => { node.disabled = !enabled; });
    this.$('#b9-save-preferences').disabled = !enabled || !capabilities?.audit?.enabled;
    this.$('#b9-camera-start').disabled = !enabled || !this.capabilities?.camera_allowed;
    this.$('#b9-state').textContent = enabled ? 'SESIÓN LOCAL' : 'SIN SESIÓN';
    this.archive.render(this.capabilities?.memory, enabled);
    if (!enabled && (this.camera.stream || this.camera.state === 'requesting')) this.camera.stop();
    if (enabled && this.capabilities) void this.connect();
  }
  accept(result, { announce = true } = {}) {
    if (!this.active()) return false;
    this.archive.note(result);
    if (result.panel) this.workspace.accept(result.panel);
    let speak = true;
    for (const command of result.ui_commands || []) {
      if (command.type === 'navigate' && ['central', 'system', 'activity', 'assistant', 'history', 'network'].includes(command.view)) this.navigate(command.view);
      else if (command.type === 'presence' || command.type === 'work') {
        window.dispatchEvent(new CustomEvent('thorn-mode-request', { detail: command.type }));
      } else if (command.type === 'stop_response') { this.cancelResponse(); speak = false; }
      else if (command.type === 'clear_context') {
        this.workspace.clear(); this.document = ''; speak = false;
        this.$('#b9-document-text').textContent = ''; this.$('#b9-question').value = ''; this.$('#b9-file').value = '';
      }
    }
    this.onReply(result, { announce: announce && speak });
    if (Array.isArray(result.sources)) this.onSources(result.sources);
    if (result.document) { this.document = result.document.id; this.$('#b9-doc-select').value = this.document; this.showDocument(); }
    window.dispatchEvent(new CustomEvent('thorn-operator-result', { detail: result }));
    return speak;
  }
  async upload() {
    const file = this.$('#b9-file').files[0];
    if (!file || file.size > 8 * 1024 * 1024) throw new Error('document_size_limit');
    const generation = this.generation;
    const credential = await this.credential();
    const response = await fetch('/api/v1/documents/upload/', { method: 'POST', headers: {
      Authorization: `Bearer ${credential}`, 'Content-Type': 'application/pdf',
      'X-Thorn-Document-Consent': 'local', 'X-Thorn-Document-Name': encodeURIComponent(file.name),
    }, body: file, cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(20000) });
    const result = await response.json();
    if (generation !== this.generation || !this.active()) return;
    if (!response.ok) throw new Error(result.error || 'document_invalid_pdf');
    this.$('#b9-file').value = ''; this.accept(result);
  }
  async downloadDocument() {
    if (!this.document) throw new Error('document_not_found');
    const generation = this.generation, credential = await this.credential();
    const response = await fetch(`/api/v1/documents/${this.document}/download/`, {
      headers: { Authorization: `Bearer ${credential}` }, cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error('document_not_found');
    const blob = await response.blob();
    if (generation !== this.generation || !this.active()) return;
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = 'thorn-documento.pdf'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  showDocument() {
    const panel = this.workspace.panels.find(p => p.kind === 'document' && p.data.id === this.document);
    this.$('#b9-document-text').textContent = panel ? panel.data.pages.map(p => `[Página ${p.page}]\n${p.text}`).join('\n\n') || 'Sin texto extraíble.' : '';
  }
  renderPanels(panels) {
    if (!this.$) return;
    const list = this.$('#b9-panels');
    list.replaceChildren(...panels.map(panel => {
      const row = document.createElement('div'); row.className = 'b9-controls';
      const title = document.createElement('span'); title.textContent = `${panel.title} / ${panel.state}`;
      row.append(title);
      for (const [label, operation] of [['Seleccionar', 'focus'], [panel.state === 'minimized' ? 'Restaurar' : 'Minimizar', panel.state === 'minimized' ? 'restore' : 'minimize'], ['Cerrar', 'close']]) {
        const button = document.createElement('button'); button.className = 'text-button'; button.textContent = label;
        button.onclick = () => this.perform(async () => { await this.workspace.change(panel.id, operation); this.showDocument(); }); row.append(button);
      }
      return row;
    }));
    const select = this.$('#b9-doc-select');
    const selected = this.document;
    select.replaceChildren(new Option('Selecciona un documento', ''), ...panels.filter(p => p.kind === 'document').map(p => new Option(p.title, p.data.id)));
    if (panels.some(p => p.kind === 'document' && p.data.id === selected)) select.value = selected;
    window.dispatchEvent(new CustomEvent('thorn-workspace-change', { detail: { panels, focused: this.workspace?.focused } }));
  }
  gesture(value) {
    if (value.state === 'off' || value.state === 'waiting') this.dragPosition = null;
    const labels = { waiting: 'Sin mano detectada', hold_palm: 'Mantén la palma abierta', cursor: 'Cursor local', dragging: 'Pinza activa', tracking: 'Esperando mano' };
    this.$('#b9-gesture-state').textContent = value.state === 'off' ? '' : `${labels[value.state] || 'Sin seguimiento'}${Number.isFinite(value.x) ? ` / ${(value.x * 100).toFixed(0)} %, ${(value.y * 100).toFixed(0)} %` : ''}`;
    window.dispatchEvent(new CustomEvent('thorn-gesture', { detail: value }));
  }
  disconnect() {
    this.generation++; this.connected = false; this.camera.stop(); this.workspace.clear();
    this.archive.disconnect();
    this.document = ''; this.dragPosition = null;
    this.controls.filter(input => input.tagName === 'INPUT').forEach(input => { if (input.type === 'checkbox') input.checked = false; else input.value = ''; });
    this.$('#b9-document-text').textContent = ''; this.$('#b9-operation').textContent = '';
  }
}
