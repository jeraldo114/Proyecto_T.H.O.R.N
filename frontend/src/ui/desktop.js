import { createElement, FileText, Camera, MapPin, X, Grid2X2, ArrowLeft, CornerDownLeft, Volume2 } from 'lucide';
import { FloatingPanels } from './floating-panels.js';
import { clamp } from './panel-layout.js';

const labels = { central: 'Presencia', system: 'Diagnóstico del equipo', activity: 'Actividad', assistant: 'Asistente y voz', history: 'Memoria e historial', network: 'Red e Internet' };
const phases = { offline: 'Sin enlace', idle: 'En espera', initializing: 'Preparando escucha', listening: 'Escuchando',
  hearing: 'Recibiendo tu voz', transcribing: 'Transcribiendo', thinking: 'Consultando el núcleo', speaking: 'Hablando',
  blocked: 'Audio listo · pulsa reproducir', error: 'Revisa el servicio' };

/** Escritorio Bastion 9. Composicion y ventanas separadas de permisos e inferencia. */
export class HudDesktop {
  constructor({ tools, scene, onView, onSpeak, onStop, credential, active, onError }) {
    Object.assign(this, { tools, scene, onView, onSpeak, onStop, credential, active, onError });
    this.view = 'central'; this.mode = 'presence'; this.panels = [];
    document.body.classList.add('hud-desktop');
    const shell = document.createElement('div'); shell.className = 'desktop-stage'; shell.id = 'desktop-stage';
    // Solo esqueleto de la interfaz. Los datos del operador y las fuentes nunca usan innerHTML.
    shell.innerHTML = `<div class="hud-frame" aria-hidden="true"></div>
      <div class="stage-coordinate stage-coordinate-top">TH / B09 <span>ENTORNO LOCAL</span></div>
      <div class="stage-coordinate stage-coordinate-bottom" aria-hidden="true">T.H.O.R.N / BASTION 9</div>
      <div id="core-mount"></div><div id="floating-space" aria-label="Escritorio de resultados"></div>
      <div id="hud-taskbar" class="hud-taskbar" aria-label="Resultados abiertos" hidden></div>
      <div class="presence-copy"><span id="hud-state" role="status">Sin enlace</span><p id="presence-greeting">Tu espacio está listo. Conecta una sesión para empezar.</p></div>
      <div id="hud-gesture-cursor" class="gesture-cursor" aria-hidden="true" hidden></div>
      <div class="radial-backdrop" id="radial-backdrop" hidden></div><nav class="radial-menu" id="radial-menu" aria-label="Accesos del núcleo" hidden></nav>
      <section id="hud-inspector" class="inspector-window" hidden><header class="inspector-header"><span>ESPACIO OPERATIVO</span><strong id="hud-inspector-title"></strong><button id="hud-close-inspector" class="window-button" aria-label="Volver al escritorio"></button></header><div id="inspector-content"></div></section>`;
    document.querySelector('.workspace').prepend(shell); this.shell = shell;
    this.area = shell.querySelector('#floating-space');
    const core = document.querySelector('.core-column'); this.core = core;
    const system = document.querySelector('#view-system'), activity = document.querySelector('#view-activity');
    system.prepend(document.querySelector('.telemetry-column')); system.append(core.querySelector('.bus-panel'), core.querySelector('.core-controls'));
    activity.append(document.querySelector('.activity-column'));
    shell.querySelector('#core-mount').append(core);
    this.coreScene = core.querySelector('#core-scene');
    this.coreScene.setAttribute('role', 'button'); this.coreScene.tabIndex = 0;
    this.coreScene.setAttribute('aria-label', 'T.H.O.R.N. Abrir menú radial. Arrastra para mover el núcleo.');
    const coreTag = document.createElement('small'); coreTag.className = 'core-identity'; coreTag.textContent = 'ASISTENTE PERSONAL'; core.querySelector('.core-caption').append(coreTag);
    const inspectors = shell.querySelector('#inspector-content');
    document.querySelectorAll('.detail-view').forEach(node => inspectors.append(node));
    const closeInspector = shell.querySelector('#hud-close-inspector'); closeInspector.append(createElement(X)); closeInspector.onclick = () => onView('central');
    this.floating = new FloatingPanels(this.area, shell.querySelector('#hud-taskbar'), {
      workspace: tools.workspace, onOpen: () => { this.onView('central'); this.setMode('work'); }, onView, onSpeak, onStop, credential, active, onError,
      internetAllowed: () => active() && document.querySelector('#internet-consent').checked,
      onTool: (name, options) => this.openTool(name, options),
      onDownload: id => { this.selectDocument(id); return tools.downloadDocument(); },
    });
    this.decorateChrome(); this.buildRadial(); this.bindCore();
    window.addEventListener('thorn-workspace-change', event => {
      this.panels = event.detail.panels;
      this.floating.update(this.panels, event.detail.focused);
      if (!this.panels.length && this.view === 'central' && ![...this.floating.windows.values()].some(record => record.local && !record.node.hidden)) this.setMode('presence');
    });
    window.addEventListener('thorn-view-change', event => this.viewChanged(event.detail));
    window.addEventListener('thorn-mode-request', event => this.setMode(event.detail));
    window.addEventListener('thorn-operator-result', event => {
      const result = event.detail;
      if (result.source === 'briefing') {
        shell.querySelector('#presence-greeting').textContent = result.reply;
        if (this.mode === 'presence') document.querySelector('#console-output').hidden = true;
      } else if (result.panel && !result.suggested_action) document.querySelector('#console-output').hidden = true;
    });
    window.addEventListener('thorn-gesture', event => this.gesture(event.detail));
    window.addEventListener('thorn-camera-state', event => {
      this.cameraBadge.hidden = !event.detail.active;
      if (event.detail.active && !this.floating.windows.has('tool-camera')) this.openTool('camera');
    });
    document.querySelector('#internet-consent').addEventListener('change', () => this.floating.consent());
    document.addEventListener('visibilitychange', () => { this.floating.visible(!document.hidden && this.mode === 'work'); if (document.hidden) this.toggleRadial(false); });
    document.addEventListener('keydown', event => {
      if (event.ctrlKey && event.key.toLowerCase() === 'k') { event.preventDefault(); document.querySelector('#command-input').focus(); }
      if (event.ctrlKey && event.key === '.') { event.preventDefault(); this.toggleRadial(); }
      if (event.key === 'F2') { event.preventDefault(); this.setMode(this.mode === 'presence' ? 'work' : 'presence'); }
      if (event.altKey && /^[1-6]$/.test(event.key)) { event.preventDefault(); onView(Object.keys(labels)[Number(event.key) - 1]); }
      if (event.key === 'Escape' && !this.radial.hidden) { event.preventDefault(); event.stopImmediatePropagation(); this.toggleRadial(false); }
    }, true);
    shell.querySelector('#radial-backdrop').onclick = () => this.toggleRadial(false);
    window.addEventListener('resize', () => { this.toggleRadial(false); this.resetCore(); });
    this.setMode('presence');
  }
  decorateChrome() {
    const brand = document.querySelector('.brand'); document.querySelector('.page-heading').prepend(brand);
    const mode = document.createElement('span'); mode.className = 'hud-mode-label'; mode.id = 'hud-mode-label'; mode.textContent = 'PRESENCIA';
    document.querySelector('.page-heading').append(mode);
    const metrics = document.createElement('div'); metrics.className = 'hud-live-metrics';
    metrics.innerHTML = '<span>CPU <b id="hud-cpu">—</b></span><span>RAM <b id="hud-memory">—</b></span><span class="hud-profile">BASTION 9</span>';
    document.querySelector('.header-actions').prepend(metrics);
    this.cameraBadge = document.createElement('button'); this.cameraBadge.className = 'camera-live-badge'; this.cameraBadge.hidden = true;
    this.cameraBadge.textContent = '● CÁMARA LOCAL · APAGAR'; this.cameraBadge.onclick = () => this.tools.camera.stop();
    document.querySelector('.header-actions').prepend(this.cameraBadge);
    const settings = document.querySelector('#settings-button'); settings.setAttribute('aria-label', 'Preferencias visuales'); settings.className = 'icon-button';
    document.querySelector('.header-actions').insertBefore(settings, document.querySelector('#focus-button'));
    document.querySelector('#focus-button').setAttribute('aria-label', 'Alternar Presencia y Trabajo');
    document.querySelector('#focus-button').dataset.tooltip = 'Presencia / Trabajo · F2';
    const nav = document.querySelector('.sidebar nav');
    for (const node of nav.querySelectorAll('[data-view]')) node.setAttribute('aria-label', labels[node.dataset.view]);
    for (const [name, title, Icon] of [['documents', 'Documentos', FileText], ['maps', 'Mapas', MapPin], ['camera', 'Cámara', Camera]]) {
      const button = document.createElement('button'); button.className = 'nav-item tool-launch'; button.dataset.tool = name;
      button.setAttribute('aria-label', title); button.dataset.tooltip = title; button.append(createElement(Icon));
      const label = document.createElement('span'); label.textContent = title; button.append(label);
      button.onclick = () => this.openTool(name); nav.append(button);
    }
    const resume = document.createElement('button'); resume.id = 'hud-audio-resume'; resume.className = 'hud-resume'; resume.hidden = true;
    resume.append(createElement(Volume2), document.createTextNode('Reproducir audio listo'));
    resume.onclick = () => document.querySelector('#resume-reply-button').click(); document.querySelector('.workspace').append(resume); this.resume = resume;
    const hint = document.createElement('span'); hint.className = 'command-shortcut'; hint.textContent = 'CTRL K'; document.querySelector('.console').append(hint);
    const back = document.createElement('button'); back.className = 'hud-back'; back.type = 'button'; back.setAttribute('aria-label', 'Volver a Presencia');
    back.append(createElement(ArrowLeft), document.createTextNode('PRESENCIA')); back.onclick = () => { this.onView('central'); this.setMode('presence'); };
    document.querySelector('.topbar').append(back);
  }
  buildRadial() {
    this.radial = this.shell.querySelector('#radial-menu');
    const entries = [
      ['Documentos', FileText, () => this.openTool('documents')], ['Mapas', MapPin, () => this.openTool('maps')],
      ['Cámara', Camera, () => this.openTool('camera')], ['Diagnóstico', Grid2X2, () => this.onView('system')],
      ['Memoria', CornerDownLeft, () => this.onView('history')], ['Preferencias', Grid2X2, () => this.openTool('preferences')],
    ];
    for (const [index, [label, Icon, action]] of entries.entries()) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'radial-item'; button.setAttribute('aria-label', label);
      const text = document.createElement('span'); text.textContent = label; button.append(createElement(Icon), text);
      const theta = index * Math.PI / 3 - Math.PI / 2;
      button.style.setProperty('--rx', `${Math.cos(theta) * 140}px`); button.style.setProperty('--ry', `${Math.sin(theta) * 140}px`);
      button.onclick = () => { this.toggleRadial(false); action(); }; this.radial.append(button);
    }
  }
  toggleRadial(value = this.radial.hidden) {
    this.radial.hidden = !value; this.shell.querySelector('#radial-backdrop').hidden = !value;
    this.coreScene.setAttribute('aria-expanded', String(value));
    if (value) {
      const rect = this.coreScene.getBoundingClientRect(), radius = 195;
      this.radial.style.left = `${clamp(rect.left + rect.width / 2, radius, innerWidth - radius)}px`;
      this.radial.style.top = `${clamp(rect.top + rect.height / 2, radius + 40, innerHeight - radius - 60)}px`;
      this.radial.querySelector('button').focus();
    }
  }
  selectDocument(id) {
    this.tools.document = id; this.tools.$('#b9-doc-select').value = id; this.tools.showDocument();
  }
  openTool(name, options = {}) {
    const group = this.tools.toolGroups.get(name); if (!group) return;
    if (options.documentId) this.selectDocument(options.documentId);
    this.onView('central'); this.setMode('work');
    if (!group._hudSlot) {
      const slot = document.createElement('button'); slot.type = 'button'; slot.className = 'command-button tool-placeholder';
      slot.textContent = `Abrir ${group.querySelector('h3').textContent} ↗`; slot.onclick = () => this.openTool(name);
      group.before(slot); group._hudSlot = slot;
    }
    const title = { documents: 'Documentos locales', maps: 'Buscar un lugar', camera: 'Cámara y gestos', preferences: 'Preferencias del operador' }[name];
    this.floating.addLocal(`tool-${name}`, title, group, { kind: name === 'camera' ? 'camera' : 'tool', onClose: operation => {
      if (name === 'camera') this.tools.camera.stop();
      if (operation === 'dispose' && group._hudSlot?.isConnected) { group._hudSlot.after(group); group._hudSlot.remove(); group._hudSlot = null; }
    } });
    if (options.documentId) requestAnimationFrame(() => this.tools.$('#b9-question').focus());
  }
  setMode(mode) {
    if (!['presence', 'work'].includes(mode)) return;
    this.mode = mode; document.body.dataset.desktopMode = mode;
    document.body.classList.toggle('focus-mode', mode === 'presence');
    document.querySelector('#focus-button').setAttribute('aria-pressed', String(mode === 'presence'));
    document.querySelector('#hud-mode-label').textContent = mode === 'presence' ? 'PRESENCIA' : 'ESPACIO DE TRABAJO';
    if (mode === 'presence') {
      this.toggleRadial(false); this.tools.camera.stop();
      if (this.view !== 'central') this.onView('central');
    }
    this.floating.visible(mode === 'work'); this.resetCore();
  }
  viewChanged(name) {
    this.view = name; document.body.dataset.desktopView = name;
    this.shell.querySelector('#hud-inspector').hidden = name === 'central';
    this.shell.querySelector('#hud-inspector-title').textContent = labels[name] || '';
    if (name !== 'central') this.setMode('work');
    this.toggleRadial(false);
  }
  resetCore() { this.core.style.removeProperty('--core-x'); this.core.style.removeProperty('--core-y'); }
  setCorePoint(x, y) {
    const size = this.coreScene.getBoundingClientRect().width / 2;
    this.core.style.setProperty('--core-x', `${clamp(x, size + 10, innerWidth - size - 10)}px`);
    this.core.style.setProperty('--core-y', `${clamp(y, size + 68, innerHeight - size - 155)}px`);
  }
  bindCore() {
    this.coreScene.addEventListener('pointerdown', event => {
      if (event.button !== 0) return; const rect = this.coreScene.getBoundingClientRect();
      this.coreDrag = { x: event.clientX, y: event.clientY, cx: rect.left + rect.width / 2, cy: rect.top + rect.height / 2, moved: false };
      this.coreScene.setPointerCapture(event.pointerId);
    });
    this.coreScene.addEventListener('pointermove', event => {
      const drag = this.coreDrag; if (!drag) return;
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 5) drag.moved = true;
      if (drag.moved) this.setCorePoint(drag.cx + event.clientX - drag.x, drag.cy + event.clientY - drag.y);
    });
    this.coreScene.addEventListener('pointerup', () => { const drag = this.coreDrag; this.coreDrag = null; if (drag && !drag.moved) this.toggleRadial(); });
    this.coreScene.addEventListener('pointercancel', () => { this.coreDrag = null; });
    this.coreScene.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); this.toggleRadial(); } });
  }
  gesture(value) {
    const cursor = this.shell.querySelector('#hud-gesture-cursor');
    if (!['cursor', 'dragging'].includes(value.state) || !Number.isFinite(value.x)) {
      cursor.hidden = true; this.gestureCore = false; this.floating.gesture(value); return;
    }
    cursor.hidden = false; cursor.style.left = `${value.x * innerWidth}px`; cursor.style.top = `${value.y * innerHeight}px`;
    cursor.dataset.dragging = String(value.dragging);
    if (value.dragging && !this.floating.gestureDrag && !this.gestureCore) {
      this.gestureCore = Boolean(document.elementFromPoint(value.x * innerWidth, value.y * innerHeight)?.closest('#core-scene'));
    }
    if (this.gestureCore && value.dragging) this.setCorePoint(value.x * innerWidth, value.y * innerHeight);
    else this.floating.gesture(value);
    if (value.released) this.gestureCore = false;
  }
  update({ state, authenticated, fresh, cpu, memory }) {
    this.scene.setState(state); this.shell.dataset.state = state;
    this.shell.querySelector('#hud-state').textContent = phases[state] || phases.idle;
    document.querySelector('#hud-cpu').textContent = authenticated && fresh && Number.isFinite(cpu) ? `${Math.round(cpu)}%` : '—';
    document.querySelector('#hud-memory').textContent = authenticated && fresh && Number.isFinite(memory) ? `${Math.round(memory)}%` : '—';
    this.resume.hidden = state !== 'blocked';
    if (!this.active()) this.floating.consent();
    if (!authenticated && this.authenticated) {
      this.floating.clear(); this.setMode('presence');
      this.shell.querySelector('#presence-greeting').textContent = 'Conecta una sesión local para continuar.';
    }
    this.authenticated = authenticated;
  }
}
