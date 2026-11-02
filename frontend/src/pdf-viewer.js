import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist';

GlobalWorkerOptions.workerSrc = '/hud/assets/pdf.worker.min.mjs';
GlobalWorkerOptions.rendererSrc = '/hud/assets/pdf.renderer.min.mjs';

/** Visor local: canvas y workers, sin ejecutar acciones o JavaScript del PDF. */
export function createPdfViewer(root, { id, credential, active, onError, onOcr }) {
  const toolbar = document.createElement('div'); toolbar.className = 'viewer-toolbar';
  const previous = button('‹', 'Página anterior'), next = button('›', 'Página siguiente');
  const smaller = button('−', 'Reducir PDF'), larger = button('+', 'Ampliar PDF');
  const count = document.createElement('span'); count.textContent = 'Cargando documento local…';
  toolbar.append(previous, count, next, smaller, larger);
  const ocrButton = button('Leer página escaneada', 'Reconocer texto de esta página con OCR local');
  const cancelOcr = button('Cancelar lectura', 'Cancelar OCR'); cancelOcr.hidden = true;
  toolbar.append(ocrButton, cancelOcr);
  const surface = document.createElement('div'); surface.className = 'pdf-surface';
  const message = document.createElement('p'); message.className = 'viewer-message';
  root.append(toolbar, surface, message);
  let pdf, loading, drawing, closing, pageNumber = 1, zoom = 1, disposed = false, visible = true, revision = 0, timer;
  const abort = new AbortController();
  let ocrAbort, ocrDrawing;
  cancelOcr.onclick = () => { ocrAbort?.abort(); ocrDrawing?.cancel(); };
  ocrButton.onclick = async () => {
    if (!pdf || disposed || !visible || !active() || ocrAbort) return;
    const controller = new AbortController(); ocrAbort = controller;
    const selectedPage = pageNumber;
    ocrButton.disabled = true; cancelOcr.hidden = false;
    message.textContent = 'Preparando lectura local de esta página…';
    let canvas;
    try {
      const page = await pdf.getPage(selectedPage), natural = page.getViewport({ scale: 1 });
      if (controller.signal.aborted || disposed) return;
      const scale = Math.min(2.1, Math.sqrt(2300000 / (natural.width * natural.height)));
      const viewport = page.getViewport({ scale });
      canvas = document.createElement('canvas'); canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      ocrDrawing = page.render({ canvas, viewport }); await ocrDrawing.promise;
      const { localOcr } = await import('/hud/assets/ocr.js');
      if (controller.signal.aborted || disposed) return;
      const result = await localOcr.recognize(canvas, { signal: controller.signal,
        onProgress: value => { if (!disposed && !controller.signal.aborted) message.textContent = `Leyendo página en tu PC… ${Math.round((value.progress || 0) * 100)} %`; } });
      if (disposed || controller.signal.aborted || !active()) return;
      if (!result.text) { message.textContent = 'No se detectó texto legible en esta página.'; return; }
      const token = await credential();
      if (disposed || controller.signal.aborted || !active()) return;
      const response = await fetch('/api/v1/documents/ocr/', { method: 'POST', headers: {
        Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, page: selectedPage, text: result.text, confidence: result.confidence }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]), credentials: 'omit', cache: 'no-store' });
      const attached = await response.json();
      if (!response.ok) throw new Error(attached.error || 'document_invalid_ocr');
      if (disposed || controller.signal.aborted || !active()) return;
      onOcr?.(attached);
      message.textContent = `Texto reconocido en la página ${selectedPage}. Ya puedes preguntarle a T.H.O.R.N.${result.confidence < 75 ? ' Revisa el texto: la confianza es baja.' : ''}${result.truncated ? ' Se usó un extracto de 3000 caracteres.' : ''} OCR lee texto; no interpreta circuitos.`;
    } catch (error) {
      if (!disposed) message.textContent = controller.signal.aborted ? 'Lectura cancelada.' : 'No se pudo leer la página. Prueba con una imagen más nítida o revisa el límite de texto.';
      if (!disposed && !controller.signal.aborted) onError?.(error);
    } finally {
      if (canvas) { canvas.width = canvas.height = 0; }
      if (ocrAbort === controller) { ocrAbort = null; ocrDrawing = null; ocrButton.disabled = false; cancelOcr.hidden = true; }
    }
  };
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
  previous.onclick = () => { if (!ocrAbort && pageNumber > 1) { pageNumber--; void render(); } };
  next.onclick = () => { if (!ocrAbort && pdf && pageNumber < pdf.numPages) { pageNumber++; void render(); } };
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
        // Un escaneo A4 de 300 dpi suele superar 4 MP. Admitimos hasta 12 MP
        // de origen; el canvas y la entrada OCR conservan sus propios límites.
        wasmUrl: '/hud/assets/', maxImageSize: 12 * 1024 * 1024, canvasMaxAreaInBytes: 16 * 1024 * 1024,
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
    visible(value) { visible = value; if (!value) { revision++; drawing?.cancel(); ocrAbort?.abort(); ocrDrawing?.cancel(); } else void render(); },
    dispose() { disposed = true; revision++; abort.abort(); drawing?.cancel(); clearTimeout(timer); observer.disconnect();
      ocrAbort?.abort(); ocrDrawing?.cancel();
      void release(); surface.replaceChildren(); },
  };
}

function button(label, description) {
  const node = document.createElement('button'); node.type = 'button'; node.className = 'viewer-button';
  node.textContent = label; node.setAttribute('aria-label', description); return node;
}
