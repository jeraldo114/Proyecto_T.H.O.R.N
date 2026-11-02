/** Helpers puros: no generan muestras ni reemplazan valores ausentes por ceros. */
export function percent(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}
export function gib(bytes) {
  return typeof bytes === 'number' && Number.isFinite(bytes) && bytes >= 0
    ? `${(bytes / 1024 ** 3).toFixed(1)} GiB` : '—';
}
export function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const value = Math.floor(seconds);
  return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map(n => String(n).padStart(2, '0')).join(':');
}
export function eventMatches(event, filter) {
  if (filter === 'runtime') return event.event_type.startsWith('runtime.');
  if (filter === 'sensor') return ['system.snapshot', 'network.snapshot'].includes(event.event_type);
  if (filter === 'error') return ['module.error', 'system.alert', 'network.web.failed'].includes(event.event_type);
  if (filter === 'command') return event.event_type === 'command.received';
  return true;
}
export function appendSample(history, sample, limit = 60) {
  if (!sample || !Number.isFinite(Date.parse(sample.sampled_at))) return history;
  if (history.some(item => item.sampled_at === sample.sampled_at)) return history;
  return [...history, { ...sample }].slice(-limit);
}
export const eventLabels = {
  'runtime.started': 'Núcleo iniciado', 'runtime.stopping': 'Cierre del núcleo',
  'system.snapshot': 'Muestra del equipo recibida', 'command.received': 'Comando recibido en el bus',
  'system.alert': 'Alerta publicada', 'module.error': 'Fallo de un módulo',
  'network.snapshot': 'Contadores de red actualizados', 'network.web.started': 'Consulta web iniciada',
  'network.web.completed': 'Consulta web completada', 'network.web.failed': 'Consulta web fallida',
  'network.web.cancelled': 'Consulta web cancelada',
};
