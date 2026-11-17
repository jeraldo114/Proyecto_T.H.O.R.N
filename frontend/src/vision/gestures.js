// Solo coordenadas del HUD: ningún gesto concede permisos ni ejecuta acciones OS.
export class GestureInterpreter {
  constructor() { this.reset(); }
  reset() {
    this.since = null; this.armed = false; this.dragging = false;
    this.pinchSince = null; this.releaseSince = null; this.lastSeen = -Infinity; this.cursor = null;
  }
  update(points, now) {
    if (!Number.isFinite(now) || now < this.lastSeen) { this.reset(); return { state: 'waiting', dragging: false, cancelled: true }; }
    const valid = Array.isArray(points) && points.length === 21 && points.every(p =>
      p && Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= -0.2 && p.x <= 1.2 && p.y >= -0.2 && p.y <= 1.2);
    if (!valid) {
      const wasDragging = this.dragging;
      this.dragging = false; this.pinchSince = this.releaseSince = this.since = null;
      if (now - this.lastSeen > 400) this.reset();
      return { state: this.armed ? 'tracking' : 'waiting', dragging: false, cancelled: wasDragging };
    }
    const distance = (a, b) => Math.hypot(points[a].x - points[b].x, points[a].y - points[b].y);
    const span = distance(5, 17);
    if (span < 0.07) return this.update([], now);
    const elapsed = now - this.lastSeen;
    if (elapsed > 400 && this.lastSeen !== -Infinity) this.reset();
    this.lastSeen = now;
    const open = [8, 12, 16, 20].every(tip => distance(0, tip) > distance(0, tip - 2) + span * 0.15);
    if (!this.armed) {
      if (!open) this.since = null;
      else if (this.since === null) this.since = now;
      else if (now - this.since >= 600) this.armed = true;
    }
    const ratio = distance(4, 8) / span, previous = this.dragging;
    // NOTA DEL OPERADOR: una pinza debe durar dos muestras, no un parpadeo
    // del detector. Umbrales diferentes al cerrar/abrir evitan el serrucho.
    if (this.armed && !this.dragging) {
      if (ratio < 0.28) {
        if (this.pinchSince === null) this.pinchSince = now;
        if (now - this.pinchSince >= 80) this.dragging = true;
      } else this.pinchSince = null;
    } else if (this.dragging) {
      if (ratio >= 0.45) {
        if (this.releaseSince === null) this.releaseSince = now;
        if (now - this.releaseSince >= 80) { this.dragging = false; this.pinchSince = null; }
      } else this.releaseSince = null;
    }
    const raw = { x: Math.max(0, Math.min(1, 1 - points[8].x)), y: Math.max(0, Math.min(1, points[8].y)) };
    if (!this.cursor || elapsed > 400) this.cursor = raw;
    else {
      const alpha = 1 - Math.exp(-Math.max(1, elapsed) / 85);
      if (Math.hypot(raw.x - this.cursor.x, raw.y - this.cursor.y) > 0.004) {
        this.cursor.x += alpha * (raw.x - this.cursor.x); this.cursor.y += alpha * (raw.y - this.cursor.y);
      }
    }
    return { state: !this.armed ? 'hold_palm' : this.dragging ? 'dragging' : 'cursor',
      ...this.cursor, dragging: this.dragging, released: previous && !this.dragging };
  }
}
