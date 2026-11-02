// Solo coordenadas del HUD: ningun gesto concede permisos ni ejecuta acciones OS.
export class GestureInterpreter {
  constructor() { this.reset(); }
  reset() { this.since = null; this.armed = false; this.dragging = false; this.lastSeen = -Infinity; }
  update(points, now) {
    if (!Number.isFinite(now)) { this.reset(); return { state: 'waiting', dragging: false }; }
    if (!Array.isArray(points) || points.length !== 21 || !Number.isFinite(now) ||
        points.some(p => !p || !Number.isFinite(p.x) || !Number.isFinite(p.y))) {
      if (now - this.lastSeen > 400) this.reset();
      return { state: this.armed ? 'tracking' : 'waiting', dragging: false };
    }
    this.lastSeen = now;
    const open = [8, 12, 16, 20].every(tip => points[tip].y < points[tip - 2].y - 0.025);
    if (!this.armed) {
      if (!open) this.since = null;
      else if (this.since === null) this.since = now;
      else if (now - this.since >= 600) this.armed = true;
    }
    const distance = (a, b) => Math.hypot(points[a].x - points[b].x, points[a].y - points[b].y);
    const ratio = distance(4, 8) / Math.max(0.02, distance(5, 17));
    const previous = this.dragging;
    this.dragging = this.armed && (previous ? ratio < 0.45 : ratio < 0.28);
    return { state: !this.armed ? 'hold_palm' : this.dragging ? 'dragging' : 'cursor',
      x: Math.max(0, Math.min(1, 1 - points[8].x)), y: Math.max(0, Math.min(1, points[8].y)),
      dragging: this.dragging, released: previous && !this.dragging };
  }
}
