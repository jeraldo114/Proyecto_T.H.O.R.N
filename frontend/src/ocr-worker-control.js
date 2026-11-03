// Control del worker nativo incluso mientras Tesseract sigue inicializándose.
let current;
export function registerOcrWorker(worker) { current = worker; return worker; }
export function terminateOcrWorker() { current?.terminate(); current = null; }
