import { createWorker } from 'tesseract.js';
import { terminateOcrWorker } from './ocr-worker-control.js';

/** Un solo OCR local, reutilizable; sin CDN, audio, disco de usuario o APIs. */
export class LocalOcr {
  constructor({ factory = createWorker, terminate = terminateOcrWorker, idleMs = 15000, timeoutMs = 45000 } = {}) {
    Object.assign(this, { factory, terminate, idleMs, timeoutMs });
    this.worker = null; this.busy = false; this.revision = 0;
  }
  async recognize(canvas, { signal, onProgress = () => {} } = {}) {
    if (this.busy) throw new Error('ocr_busy');
    if (!canvas || canvas.width < 1 || canvas.height < 1 || canvas.width * canvas.height > 2400000) throw new Error('ocr_image_limit');
    if (signal?.aborted) throw new DOMException('Cancelado', 'AbortError');
    this.busy = true; clearTimeout(this.idleTimer);
    const revision = ++this.revision;
    let deadline, rejectCancellation;
    const cancel = () => { this.stop(); rejectCancellation?.(new DOMException('Cancelado', 'AbortError')); };
    signal?.addEventListener('abort', cancel, { once: true });
    this.progress = value => { if (revision === this.revision) onProgress(value); };
    const operation = (async () => {
      if (!this.worker) {
        const worker = await this.factory('spa', 1, {
          workerPath: '/hud/assets/ocr-worker.min.js', workerBlobURL: false,
          corePath: '/hud/assets', langPath: '/hud/assets', gzip: true,
          cacheMethod: 'none', logger: value => this.progress?.(value), errorHandler: () => {},
        });
        if (revision !== this.revision) { await worker.terminate(); throw new DOMException('Cancelado', 'AbortError'); }
        this.worker = worker;
        await worker.setParameters({ tessedit_pageseg_mode: '3', user_defined_dpi: '150' });
      }
      const result = await this.worker.recognize(canvas, {}, { text: true });
      if (revision !== this.revision) throw new DOMException('Cancelado', 'AbortError');
      return { text: result.data.text.trim().slice(0, 3000), confidence: result.data.confidence,
        truncated: result.data.text.trim().length > 3000, local: true };
    })();
    try {
      return await Promise.race([operation, new Promise((_, reject) => {
        rejectCancellation = reject;
        deadline = setTimeout(() => { this.stop(); reject(new Error('ocr_timeout')); }, this.timeoutMs);
      })]);
    } catch (error) {
      this.stop(); throw error;
    } finally {
      clearTimeout(deadline); signal?.removeEventListener('abort', cancel);
      this.busy = false; this.progress = null;
      if (this.worker) this.idleTimer = setTimeout(() => this.stop(), this.idleMs);
    }
  }
  stop() {
    this.revision++; clearTimeout(this.idleTimer);
    this.terminate(); void this.worker?.terminate(); this.worker = null;
  }
}

export const localOcr = new LocalOcr();
