import { createElement, Minus, X, Maximize2, FileText, Globe, MessageSquare, Activity, Grip } from 'lucide';
import { panelPixels, panelPosition, initialPosition, clamp } from './panel-layout.js';
import { mountPanelContent } from './panel-content.js';

const kinds = { answer: ['RESPUESTA', MessageSquare], document: ['DOCUMENTO LOCAL', FileText],
  map: ['CARTOGRAFÍA', Globe], system: ['DIAGNÓSTICO', Activity], briefing: ['RESUMEN', Activity], sources: ['FUENTES', Globe], memory: ['MEMORIA', FileText] };

/** Ventanas de resultados: movimiento local fluido y una escritura al soltar. */
export class FloatingPanels {
  constructor(area, taskbar, services) {
    Object.assign(this, { area, taskbar, services }); this.windows = new Map(); this.level = 5; this.enabled = true;
    this.observer = new ResizeObserver(() => { for (const record of this.windows.values()) this.place(record); });
    this.observer.observe(area);
  }
  update(panels, focused) {
    const wanted = new Set(panels.map(panel => panel.id));
    for (const [id, record] of this.windows) {
      if (record.local || wanted.has(id)) continue;
      if (this.gestureDrag?.record === record) this.gestureDrag = null;
      record.content?.dispose(); record.observer.disconnect(); record.node.remove(); this.windows.delete(id);
    }
    for (const [index, panel] of panels.entries()) {
      let record = this.windows.get(panel.id);
      if (!record) {
        record = this.create(panel, index); this.windows.set(panel.id, record);
        if (panel.kind !== 'briefing') this.services.onOpen();
      }
      record.panel = panel;
      if (panel.position && (panel.position.x !== 0.1 || panel.position.y !== 0.1 || record.moved)) record.position = panel.position;
      record.node.hidden = panel.state === 'minimized';
      record.node.classList.toggle('is-focused', panel.id === focused);
      if (panel.id === focused) record.node.style.zIndex = String(++this.level);
      const visible = !record.node.hidden && this.enabled && !document.hidden;
      if (record.visible !== visible) { record.visible = visible; record.content?.visible(visible); }
      this.place(record);
    }
    this.renderTaskbar(panels);
  }
  create(panel, index, local = false) {
    const node = document.createElement('article'); node.className = `floating-panel panel-${panel.kind}`;
    node.dataset.panelId = panel.id; node.setAttribute('role', 'region'); node.setAttribute('aria-label', panel.title);
    const header = document.createElement('header'); header.className = 'floating-header'; header.tabIndex = 0;
    header.setAttribute('aria-label', `Mover ${panel.title}. Usa las flechas del teclado.`);
    const [kind, Icon] = kinds[panel.kind] || ['HERRAMIENTA', Grip]; header.append(createElement(Icon));
    const label = document.createElement('div'), category = document.createElement('small'), title = document.createElement('strong');
    category.textContent = kind; title.textContent = panel.title; label.append(category, title); header.append(label);
    const actions = document.createElement('div'); actions.className = 'window-actions';
    for (const [name, description, icon] of [['minimize', 'Minimizar', Minus], ['maximize', 'Ampliar ventana', Maximize2], ['close', 'Cerrar', X]]) {
      const button = document.createElement('button'); button.className = 'window-button'; button.type = 'button';
      button.dataset.windowAction = name; button.setAttribute('aria-label', `${description}: ${panel.title}`); button.append(createElement(icon));
      button.onclick = () => this.action(panel.id, name); actions.append(button);
    }
    header.append(actions);
    const content = document.createElement('div'); content.className = 'floating-content';
    const footer = document.createElement('footer'); footer.className = 'floating-footer';
    const source = document.createElement('span'); source.textContent = local ? 'CONTROL LOCAL' : panel.source === 'internet' ? 'FUENTES WEB' : panel.kind === 'map' ? 'WIKIPEDIA / OPENSTREETMAP' : 'SESIÓN LOCAL';
    const time = document.createElement('time'); const stamp = Date.parse(panel.created_at);
    time.textContent = Number.isFinite(stamp) ? new Date(stamp).toLocaleTimeString('es-CL', { hourCycle: 'h23' }) : '';
    footer.append(source, time); node.append(header, content, footer); this.area.append(node);
    const record = { node, panel, header, body: content, local, position: initialPosition(panel.kind, index), moved: false };
    if (!local) record.content = mountPanelContent(panel, content, this.services);
    record.observer = new ResizeObserver(() => { if (!record.dragging) this.place(record); }); record.observer.observe(node);
    header.addEventListener('pointerdown', event => this.beginDrag(event, record));
    header.addEventListener('pointermove', event => this.drag(event, record));
    header.addEventListener('pointerup', event => this.endDrag(event, record));
    header.addEventListener('pointercancel', event => this.endDrag(event, record));
    header.addEventListener('keydown', event => {
      if (event.target !== header || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); const rect = record.node.getBoundingClientRect(), bounds = this.area.getBoundingClientRect(), step = event.shiftKey ? 48 : 12;
      this.move(record, { x: rect.left - bounds.left + (event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0),
        y: rect.top - bounds.top + (event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0) }); this.commit(record);
    });
    node.addEventListener('pointerdown', () => this.focus(record), { passive: true });
    return record;
  }
  addLocal(id, title, element, { kind = 'tool', onClose } = {}) {
    let record = this.windows.get(id);
    if (!record) {
      const panel = { id, title, kind, state: 'open', source: 'local' };
      record = this.create(panel, this.windows.size, true); record.onClose = onClose;
      record.body.append(element); element.hidden = false; this.windows.set(id, record);
    }
    record.node.hidden = false; this.focus(record); this.place(record); this.services.onOpen(); return record;
  }
  focus(record) {
    for (const item of this.windows.values()) item.node.classList.toggle('is-focused', item === record);
    record.node.style.zIndex = String(++this.level); this.focused = record.panel.id;
    if (!record.local) this.services.workspace.focused = record.panel.id;
  }
  action(id, action) {
    const record = this.windows.get(id); if (!record) return;
    if (action === 'maximize') { record.node.classList.toggle('is-maximized'); record.content?.visible(true); return; }
    if (record.local) {
      record.node.hidden = true; record.onClose?.(action); return;
    }
    void this.services.workspace.change(id, action).catch(this.services.onError);
  }
  place(record) {
    if (record.dragging || record.node.classList.contains('is-maximized')) return;
    const area = this.area.getBoundingClientRect(), size = record.node.getBoundingClientRect();
    const point = panelPixels(record.position, area, size);
    record.node.style.left = `${point.x}px`; record.node.style.top = `${point.y}px`;
  }
  beginDrag(event, record) {
    if (event.button !== 0 || event.target.closest('button') || record.node.classList.contains('is-maximized')) return;
    const rect = record.node.getBoundingClientRect(); record.dragging = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    record.header.setPointerCapture(event.pointerId); record.node.classList.add('is-dragging'); this.focus(record); event.preventDefault();
  }
  drag(event, record) {
    if (!record.dragging) return;
    const area = this.area.getBoundingClientRect();
    this.move(record, { x: event.clientX - area.left - record.dragging.x, y: event.clientY - area.top - record.dragging.y });
  }
  move(record, point) {
    const area = this.area.getBoundingClientRect(), size = record.node.getBoundingClientRect();
    record.position = panelPosition(point, area, size); record.moved = true;
    const pixel = panelPixels(record.position, area, size);
    record.node.style.left = `${pixel.x}px`; record.node.style.top = `${pixel.y}px`;
  }
  endDrag(event, record) {
    if (!record.dragging) return;
    record.dragging = null; record.node.classList.remove('is-dragging');
    if (record.header.hasPointerCapture(event.pointerId)) record.header.releasePointerCapture(event.pointerId);
    this.commit(record);
  }
  commit(record) {
    if (!record.local) void this.services.workspace.change(record.panel.id, 'move', record.position).catch(this.services.onError);
  }
  renderTaskbar(panels) {
    this.taskbar.replaceChildren(...panels.map(panel => {
      const button = document.createElement('button'); button.className = 'task-chip'; button.type = 'button';
      button.textContent = panel.title; button.dataset.panelId = panel.id;
      button.setAttribute('aria-label', `${panel.state === 'minimized' ? 'Restaurar' : 'Enfocar'}: ${panel.title}`);
      button.dataset.minimized = String(panel.state === 'minimized');
      button.onclick = () => {
        this.services.onOpen(); void this.services.workspace.change(panel.id, panel.state === 'minimized' ? 'restore' : 'focus').catch(this.services.onError);
      }; return button;
    }));
    this.taskbar.hidden = panels.length === 0;
  }
  visible(value) {
    this.enabled = value;
    for (const record of this.windows.values()) {
      const visible = value && !record.node.hidden && !document.hidden;
      if (record.visible !== visible) { record.visible = visible; record.content?.visible(visible); }
    }
  }
  consent() { for (const record of this.windows.values()) record.content?.consent?.(); }
  gesture(value) {
    if (!value.dragging && !value.released) { this.gestureDrag = null; return; }
    if (!Number.isFinite(value.x) || !Number.isFinite(value.y)) return;
    const x = clamp(value.x, 0, 1) * innerWidth, y = clamp(value.y, 0, 1) * innerHeight;
    if (value.dragging && !this.gestureDrag) {
      const hit = document.elementFromPoint(x, y)?.closest('[data-panel-id]');
      const record = this.windows.get(hit?.dataset.panelId) || this.windows.get(this.services.workspace.focused);
      if (!record || record.node.hidden) return;
      const rect = record.node.getBoundingClientRect(); this.gestureDrag = { record, x: x - rect.left, y: y - rect.top };
      this.focus(record);
    }
    if (this.gestureDrag && value.dragging) {
      const area = this.area.getBoundingClientRect(), drag = this.gestureDrag;
      this.move(drag.record, { x: x - area.left - drag.x, y: y - area.top - drag.y });
    }
    if (value.released && this.gestureDrag) { this.commit(this.gestureDrag.record); this.gestureDrag = null; }
  }
  clear() {
    for (const record of this.windows.values()) {
      record.content?.dispose(); record.observer.disconnect(); record.node.remove(); record.onClose?.('dispose');
    }
    this.windows.clear(); this.taskbar.replaceChildren(); this.taskbar.hidden = true; this.gestureDrag = null;
  }
}
