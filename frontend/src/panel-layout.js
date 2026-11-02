/** Coordenadas relativas: una ventana siempre conserva un encabezado alcanzable. */
export function clamp(value, minimum, maximum) {
  return Number.isFinite(value) ? Math.max(minimum, Math.min(Math.max(minimum, maximum), value)) : minimum;
}
export function panelPixels(position, area, size) {
  return { x: clamp(position?.x, 0, 1) * Math.max(0, area.width - size.width),
    y: clamp(position?.y, 0, 1) * Math.max(0, area.height - size.height) };
}
export function panelPosition(pixel, area, size) {
  return { x: clamp(pixel.x / Math.max(1, area.width - size.width), 0, 1),
    y: clamp(pixel.y / Math.max(1, area.height - size.height), 0, 1) };
}
export function initialPosition(kind, index) {
  if (kind === 'map') return { x: 0.1, y: 0.12 };
  if (kind === 'document') return { x: 0.34, y: 0.24 };
  if (kind === 'briefing') return { x: 0.05, y: 0.15 };
  return { x: 0.6 + (index % 2) * 0.08, y: 0.18 + (index % 3) * 0.14 };
}
