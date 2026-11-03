import { GestureInterpreter } from './gestures.js';

/** Camara local optativa. Las imagenes solo pasan al worker de este navegador. */
export class LocalCamera {
  constructor(video, { onState = () => {}, onGesture = () => {},
    getMedia = options => navigator.mediaDevices.getUserMedia(options),
    createWorker = () => new Worker('/hud/assets/vision-worker.js', { type: 'module' }),
    createImage = element => createImageBitmap(element) } = {}) {
    Object.assign(this, { video, onState, onGesture, getMedia, createWorker, createImage });
    this.gesture = new GestureInterpreter(); this.revision = 0; this.state = 'off';
    this.stream = null; this.worker = null; this.busy = false;
  }
  notify(state) { this.state = state; this.onState(state); }
  async start({ consent = false } = {}) {
    if (consent !== true) throw new Error('camera_consent_required');
    this.stop(); const revision = this.revision;
    this.notify('requesting');
    try {
      const stream = await this.getMedia({ audio: false, video: { width: { ideal: 640, max: 960 },
        height: { ideal: 480, max: 720 }, frameRate: { ideal: 15, max: 15 }, facingMode: 'user' } });
      if (revision !== this.revision) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream; this.video.srcObject = stream;
      stream.getVideoTracks().forEach(track => { track.onended = () => this.stop('device_lost'); });
      await this.video.play();
      if (revision !== this.revision) return;
      this.notify('preview');
    } catch (error) {
      if (revision === this.revision) this.stop(error.name === 'NotAllowedError' ? 'permission_required' : 'camera_error');
    }
  }
  async enableGestures() {
    if (!this.stream || this.worker) return;
    const revision = this.revision;
    const worker = this.createWorker(); this.worker = worker;
    this.notify('loading_model');
    worker.onerror = () => { if (revision === this.revision) this.stopGestures('vision_error'); };
    worker.onmessage = event => {
      if (revision !== this.revision || worker !== this.worker) return;
      if (event.data.type === 'ready') {
        clearTimeout(this.loadTimer); this.notify('tracking'); this.schedule(revision);
      } else if (event.data.type === 'result') {
        clearTimeout(this.frameTimer);
        this.busy = false;
        this.onGesture(this.gesture.update(event.data.landmarks, event.data.timestamp));
        this.schedule(revision);
      } else { this.stopGestures(event.data.code || 'vision_error'); }
    };
    this.loadTimer = setTimeout(() => { if (revision === this.revision) this.stopGestures('vision_timeout'); }, 15000);
    worker.postMessage({ type: 'init' });
  }
  schedule(revision) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.frame(revision); }, 125);
  }
  async frame(revision) {
    if (revision !== this.revision || !this.worker || this.busy) return;
    if (this.video.readyState < 2) { this.schedule(revision); return; }
    this.busy = true;
    const worker = this.worker;
    this.frameTimer = setTimeout(() => {
      if (revision === this.revision && worker === this.worker) this.stopGestures('vision_timeout');
    }, 2500);
    try {
      const image = await this.createImage(this.video);
      if (revision !== this.revision || worker !== this.worker) { image.close(); return; }
      worker.postMessage({ type: 'frame', image, timestamp: performance.now() }, [image]);
    } catch { if (revision === this.revision) this.stopGestures('vision_error'); }
  }
  stopGestures(state = 'preview') {
    clearTimeout(this.timer); clearTimeout(this.loadTimer); clearTimeout(this.frameTimer);
    this.worker?.terminate(); this.worker = null; this.busy = false;
    this.gesture.reset(); this.onGesture({ state: 'off', dragging: false });
    if (this.stream) this.notify(state);
  }
  stop(state = 'off') {
    this.revision++; this.stopGestures();
    this.stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    this.stream = null; this.video.pause(); this.video.srcObject = null;
    this.notify(state);
  }
}
