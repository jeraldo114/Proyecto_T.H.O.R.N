import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist';

GlobalWorkerOptions.workerSrc = '/hud/assets/pdf.worker.min.mjs';
GlobalWorkerOptions.rendererSrc = '/hud/assets/pdf.renderer.min.mjs';

/** Visor local: canvas y workers, sin ejecutar acciones o JavaScript del PDF. */
export function createPdfViewer(root, { id, credential, active, onError }) {
  const toolbar = document.createElement('div'); toolbar.className = 'viewer-toolbar';
  const previous = button('‹', 'Página anterior'), next = button('›', 'Página siguiente');
  const smaller = button('−', 'Reducir PDF'), larger = button('+', 'Ampliar PDF');
  const count = document.createElement('span'); count.textContent = 'Cargando documento local…';
  toolbar.append(previous, count, next, smaller, larger);
  const surface = document.createElement('div'); surface.className = 'pdf-surface';
  const message = document.createElement('p'); message.className = 'viewer-message';
  root.append(toolbar, surface, message);
  let pdf, loading, drawing, closing, pageNumber = 1, zoom = 1, disposed = false, visible = true, revision = 0, timer;
  const abort = new AbortController();
  function release() {
    // PDF.js 6 libera documento, transporte y renderizador desde LoadingTask.
    // No invocar destroy() sobre PDFDocumentProxy ni destruir dos veces en una carrera.
    if (!closing && loading) closing = loading.destroy().catch(() => {});
    return closing;
  }
  async function render() {
    if (!pdf || disposed || !visible) return;
    const generation = ++revision;
    drawing?.cancel();
    try {
      const page = await pdf.getPage(pageNumber);
      if (generation !== revision || disposed) return;
      const natural = page.getViewport({ scale: 1 });
      const fit = Math.min(Math.max(160, surface.clientWidth - 32) / natural.width,
        Math.max(120, surface.clientHeight - 26) / natural.height);
      const scale = Math.min(fit * zoom,
        2048 / natural.width, 2048 / natural.height);
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement('canvas'); canvas.setAttribute('aria-label', `Página ${pageNumber} del PDF`);
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      surface.replaceChildren(canvas);
      drawing = page.render({ canvas, viewport });
      await drawing.promise;
      if (generation !== revision || disposed) return;
      canvas.dataset.rendered = 'true';
      count.textContent = `${pageNumber} / ${pdf.numPages}`;
      previous.disabled = pageNumber === 1; next.disabled = pageNumber === pdf.numPages;
      smaller.disabled = zoom <= 0.5; larger.disabled = zoom >= 2;
      message.textContent = 'PDF LOCAL · Usa la consulta del documento para preguntar sobre su texto.';
    } catch (error) {
      if (!disposed && generation === revision && error.name !== 'RenderingCancelledException') {
        message.textContent = 'No se pudo representar esta página. El texto extraído sigue disponible.';
      }
    }
  }
  previous.onclick = () => { if (pageNumber > 1) { pageNumber--; void render(); } };
  next.onclick = () => { if (pdf && pageNumber < pdf.numPages) { pageNumber++; void render(); } };
  smaller.onclick = () => { zoom = Math.max(0.5, zoom - 0.25); void render(); };
  larger.onclick = () => { zoom = Math.min(2, zoom + 0.25); void render(); };
  const host = root.closest('.floating-content') || root;
  let observedWidth = 0, observedHeight = 0;
  const observer = new ResizeObserver(() => {
    const width = host.clientWidth, height = host.clientHeight;
    if (Math.abs(width - observedWidth) < 2 && Math.abs(height - observedHeight) < 2) return;
    observedWidth = width; observedHeight = height;
    surface.style.height = `${Math.max(140, height - 140)}px`;
    clearTimeout(timer); timer = setTimeout(() => void render(), 160);
  });
  observer.observe(host);
  void (async () => {
    try {
      const token = await credential();
      if (disposed || !active()) return;
      const response = await fetch(`/api/v1/documents/${id}/download/`, { headers: { Authorization: `Bearer ${token}` },
        credentials: 'omit', cache: 'no-store', signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]) });
      if (!response.ok) throw new Error('document_not_found');
      const data = new Uint8Array(await response.arrayBuffer());
      if (disposed || !active()) return;
      if (data.byteLength > 8 * 1024 * 1024) throw new Error('document_size_limit');
      loading = getDocument({ data, isEvalSupported: false, enableXfa: false,
        cMapUrl: '/hud/assets/', cMapPacked: true, standardFontDataUrl: '/hud/assets/',
        wasmUrl: '/hud/assets/', maxImageSize: 4 * 1024 * 1024, canvasMaxAreaInBytes: 16 * 1024 * 1024,
        useSystemFonts: true, useWorkerFetch: true });
      const limit = setTimeout(() => { if (!pdf) { void release(); message.textContent = 'El visor superó el tiempo de carga.'; } }, 15000);
      try { pdf = await loading.promise; } finally { clearTimeout(limit); }
      if (disposed) { void release(); return; }
      await render();
    } catch (error) {
      if (!disposed) { message.textContent = 'Visor no disponible. Puedes leer el texto o descargar el original.'; onError?.(error); }
    }
  })();
  return {
    visible(value) { visible = value; if (!value) { revision++; drawing?.cancel(); } else void render(); },
    dispose() { disposed = true; revision++; abort.abort(); drawing?.cancel(); clearTimeout(timer); observer.disconnect();
      void release(); surface.replaceChildren(); },
  };
}

function button(label, description) {
  const node = document.createElement('button'); node.type = 'button'; node.className = 'viewer-button';
  node.textContent = label; node.setAttribute('aria-label', description); return node;
}
