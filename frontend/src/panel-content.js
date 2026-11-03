import { renderSources } from './network.js';

/** Contenido seguro: el servidor entrega datos; el escritorio decide como dibujarlos. */
export function mountPanelContent(panel, root, services) {
  let controller, disposed = false, visible = true;
  const data = panel.data || {};
  const text = (value, className = 'panel-prose') => {
    const node = document.createElement('p'); node.className = className;
    node.textContent = typeof value === 'string' ? value : ''; root.append(node); return node;
  };
  function button(label, callback, parent = root) {
    const node = document.createElement('button'); node.className = 'viewer-button'; node.type = 'button'; node.textContent = label;
    node.onclick = () => { void Promise.resolve().then(callback).catch(services.onError); }; parent.append(node); return node;
  }
  const attach = value => { if (disposed) value?.dispose(); else { controller = value; controller?.visible?.(visible); } };
  if (panel.kind === 'answer' || panel.kind === 'sources') {
    text(data.text);
    if (panel.kind === 'sources') { const list = document.createElement('ul'); list.className = 'web-sources'; renderSources(list, data.sources); root.append(list); }
    const actions = document.createElement('div'); actions.className = 'panel-inline-actions'; root.append(actions);
    button('Leer respuesta', () => services.onSpeak(data.text), actions);
    button('Detener voz', services.onStop, actions);
  } else if (panel.kind === 'map') {
    void import('/hud/assets/map-viewer.js').then(module => {
      if (!disposed) attach(module.createMapViewer(root, { places: data.places, allowed: services.internetAllowed, onError: services.onError }));
    }).catch(() => { if (!disposed) text('No se pudo iniciar el mapa. Las fuentes siguen disponibles en Red e Internet.'); });
  } else if (panel.kind === 'document') {
    const tools = document.createElement('div'); tools.className = 'viewer-toolbar'; root.append(tools);
    const pdf = document.createElement('div'); pdf.className = 'pdf-viewer';
    const extracted = document.createElement('pre'); extracted.className = 'document-extracted'; extracted.hidden = true;
    extracted.textContent = (data.pages || []).map(page => `[Página ${page.page}]\n${page.text}`).join('\n\n') || 'Sin texto extraíble. Usa «Leer página escaneada» para reconocer el texto localmente.';
    button('Documento', () => { pdf.hidden = false; extracted.hidden = true; controller?.visible(visible); }, tools);
    button('Texto', () => { pdf.hidden = true; extracted.hidden = false; controller?.visible(false); }, tools);
    button('Preguntar', () => services.onTool('documents', { documentId: data.id }), tools);
    button('Descargar', () => services.onDownload(data.id), tools);
    root.append(pdf, extracted);
    void import('/hud/assets/pdf-viewer.js').then(module => {
      if (!disposed) attach(module.createPdfViewer(pdf, { id: data.id, credential: services.credential, active: services.active, onError: services.onError,
        onOcr: result => {
          extracted.textContent = result.document.pages.map(page => `[Página ${page.page}]\n${page.text}`).join('\n\n');
          window.dispatchEvent(new CustomEvent('thorn-document-ocr', { detail: result }));
        } }));
    }).catch(() => { if (!disposed) { pdf.hidden = true; extracted.hidden = false; text('Visor no disponible. Se muestra el texto extraído.'); } });
  } else if (panel.kind === 'system' || panel.kind === 'briefing') {
    if (data.local_time) text(new Date(data.local_time).toLocaleString('es-CL', { timeZone: data.timezone }), 'briefing-time');
    const fresh = data.telemetry_fresh === true;
    text(fresh ? 'Muestra del equipo disponible al crear este resultado.' : 'Sin telemetría vigente al crear este resultado.', 'panel-muted');
    const telemetry = data.telemetry;
    if (telemetry && fresh) {
      const list = document.createElement('dl'); list.className = 'panel-metrics';
      for (const [key, label, suffix] of [['cpu_percent', 'CPU', '%'], ['memory_percent', 'MEMORIA', '%'], ['disk_percent', 'DISCO', '%']]) {
        const item = document.createElement('div'), title = document.createElement('dt'), value = document.createElement('dd');
        title.textContent = label; value.textContent = Number.isFinite(telemetry[key]) ? `${telemetry[key]}${suffix}` : '—'; item.append(title, value); list.append(item);
      }
      root.append(list);
    }
    const alerts = Array.isArray(data.active_alerts) ? data.active_alerts.length : 0;
    text(alerts ? `${alerts} alerta(s) registradas. Consulta su vigencia en Sistema.` : 'Sin alertas registradas en este resultado.', 'panel-muted');
    button('Abrir diagnóstico', () => services.onView('system'));
  } else if (panel.kind === 'memory') {
    for (const note of data.notes || []) text(note.text);
  }
  return {
    visible(value) { visible = value; controller?.visible?.(value); },
    consent() { controller?.consent?.(); },
    dispose() { disposed = true; controller?.dispose?.(); root.replaceChildren(); },
  };
}
