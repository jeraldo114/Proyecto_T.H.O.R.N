import { WAKE_ALIASES } from './wake-word.js';

const WAKE_PREFIX = new RegExp(`^(?:${WAKE_ALIASES.join('|')})[,:]?\\s+`, 'iu');

export function rate(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(2)} MiB/s` : `${(value / 1024).toFixed(1)} KiB/s`;
}

/** Solo ordenes explicitas salen a la red; el modelo no decide destinos. */
export function networkIntent(prompt) {
  const value = prompt.trim().replace(WAKE_PREFIX, '');
  const research = /^investiga(?: en (?:internet|la web))?\s+(?:sobre\s+)?(.+)$/iu.exec(value);
  if (research) return { mode: 'search', value: research[1].trim(), investigate: true };
  const search = /^(?:busca|buscar|consulta|investiga)(?:\s+en)?\s+(?:internet|la web|wikipedia)\s+(?:sobre\s+)?(.+)$/iu.exec(value);
  if (search) return { mode: 'search', value: search[1].trim() };
  const read = /^(?:lee|leer|resume|resumir)(?:\s+esta\s+p[aá]gina)?\s+(https:\/\/\S+)$/iu.exec(value);
  return read ? { mode: 'read', value: read[1] } : null;
}

export function topicFollowUp(pending, prompt, now) {
  if (!pending || now >= pending.expires) return null;
  const value = prompt.trim().replace(/^[¿¡]+\s*/u, '').replace(WAKE_PREFIX, '')
    .replace(/^sobre\s+/iu, '').replace(/[.!?]+$/u, '').trim();
  if (!value || value.length > 160 || /[\r\n]/u.test(value) ||
      /^(?:no|cancela|cambiar|espera|silencio|detente|para|abre|muestra|pon|revisa|olvida|borra|retoma|investiga|busca|lee|ejecuta|estado|sistema|limpiar|dime|expl[ií]came|qu[eé]|cu[aá]l|c[oó]mo|cu[aá]ndo)(?:\s|[,.:;!?]|$)/iu.test(value)) return null;
  return pending.kind === 'resume' ? { prompt: `Retoma el tema de ${value}` }
    : pending.kind === 'research' ? { intent: { mode: 'search', value, investigate: true } } : null;
}

const STAGES = {
  offline: () => 'Sin red local: ninguna interfaz activa tiene una dirección utilizable (solo loopback o 169.254). Revisa el Wi-Fi o el cable.',
  dns: host => `El DNS no respondió para ${host}. Hay red local, pero la resolución de nombres falla: revisa el router o el servidor DNS.`,
  dns_private: host => `${host} resolvió a una dirección privada. Suele ser un portal cautivo (Wi-Fi de hotel o café) o un DNS que filtra.`,
  connect: host => `El DNS funciona, pero no se abrió la conexión con ${host}. Un cortafuegos o el router puede estar bloqueando HTTPS.`,
  tls: host => `Se llegó a ${host}, pero su certificado TLS no fue válido. Puede haber un proxy que intercepta o la hora del equipo está mal.`,
  timeout: host => `${host} no respondió a tiempo: la conexión es lenta o inestable.`,
};

/** Texto del diagnóstico por etapas: dice dónde falló, no solo que falló. */
export function describeDiagnosis(result) {
  const when = Number.isFinite(Date.parse(result.checked_at)) ? new Date(result.checked_at).toLocaleString('es-CL') : '—';
  const lines = [];
  for (const check of result.checks?.length ? result.checks : [{ host: 'example.com', ...result }]) {
    if (check.reachable) lines.push(`${check.host}: DNS ${check.dns_ms} ms / HTTPS ${check.https_ms} ms${check.failed_stage === 'http' ? ' (respondió sin una página útil)' : ''}.`);
    else lines.push(STAGES[check.failed_stage]?.(check.host) || `${check.host}: no se pudo completar la prueba.`);
  }
  const summary = result.failed_stage === 'offline' ? 'SIN RED LOCAL'
    : result.reachable ? `DNS ${result.dns_ms} ms / HTTPS ${result.https_ms} ms` : `FALLO: ${String(result.failed_stage || 'desconocido').toUpperCase()}`;
  return { summary, reply: `${result.reachable ? 'Prueba HTTPS completada.' : 'Prueba HTTPS con problemas.'}\n${lines.join('\n')}\nMedido: ${when}\nEs una prueba de esos destinos, no una medición ICMP ni de todo Internet.` };
}

export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port && url.port !== '443') return null;
    if (!url.hostname.includes('.') || /^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.)/i.test(url.hostname)
      || /^(172\.(1[6-9]|2\d|3[01])\.)/.test(url.hostname)) return null;
    return url.href;
  } catch { return null; }
}

export function renderSources(container, sources, compact = false) {
  const nodes = [];
  for (const [index, source] of (Array.isArray(sources) ? sources.slice(0, 3) : []).entries()) {
    const href = safeSourceUrl(source.url);
    if (!href || typeof source.title !== 'string') continue;
    const item = document.createElement(compact ? 'div' : 'li'); item.className = 'web-source';
    const link = document.createElement('a'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = `[${index + 1}] ${source.title}`;
    const meta = document.createElement('small'), stamp = Date.parse(source.retrieved_at);
    meta.textContent = `${new URL(href).hostname} / ${source.kind === 'page' ? 'Lectura' : 'Resultado'}${Number.isFinite(stamp) ? ` / Consultado ${new Date(stamp).toLocaleString('es-CL')}` : ''}`;
    const published = /^\d{4}-\d{2}-\d{2}$/u.test(source.published_at || '') ? source.published_at : '';
    meta.textContent += published ? ` / Publicación declarada ${published}` : ' / Publicación sin fecha disponible';
    item.append(link, meta);
    if (!compact) {
      const excerpt = document.createElement('p'); excerpt.textContent = String(source.excerpt || '').slice(0, 1200); item.append(excerpt);
    }
    nodes.push(item);
  }
  container.replaceChildren(...nodes); container.hidden = !nodes.length;
}

export function renderNetwork(data, { active, busy }) {
  const $ = selector => document.querySelector(selector);
  const snapshot = active ? data?.snapshot : null, fresh = active && data?.sensor.fresh;
  $('#network-fresh').textContent = !snapshot ? 'SIN MUESTRA' : fresh ? 'MUESTRA VIGENTE' : 'MUESTRA RETENIDA';
  $('#network-time').textContent = snapshot ? new Date(snapshot.sampled_at).toLocaleString('es-CL') : '—';
  $('#network-upload').textContent = fresh ? rate(snapshot?.upload_bps) : '—';
  $('#network-download').textContent = fresh ? rate(snapshot?.download_bps) : '—';
  $('#network-gateway').textContent = snapshot?.gateway_ipv4 || '—';
  $('#network-interfaces').replaceChildren(...(snapshot?.interfaces || []).map(item => {
    const row = document.createElement('div'); row.className = 'network-interface';
    const name = document.createElement('strong'); name.textContent = item.name;
    const state = document.createElement('span'); state.textContent = item.up ? 'ACTIVA' : 'INACTIVA';
    const address = document.createElement('span'); address.textContent = item.addresses.join(' / ') || 'Sin dirección IP';
    const traffic = document.createElement('span'); traffic.textContent = fresh ? `↑ ${rate(item.upload_bps)}  ↓ ${rate(item.download_bps)}` : '—';
    row.append(name, state, address, traffic); return row;
  }));
  const internet = active ? data?.internet : null;
  $('#internet-consent').disabled = !internet?.enabled;
  $('#search-provider').textContent = internet?.provider === 'brave' ? internet.search_available ? 'BRAVE / WEB GENERAL' : 'BRAVE / FALTA API KEY'
    : internet?.provider === 'searxng' ? 'SEARXNG LOCAL / WEB GENERAL' : internet ? 'WIKIPEDIA / ENCICLOPEDIA' : 'SIN SESIÓN';
  if (internet?.state === 'disabled') $('#search-provider').textContent = 'INTERNET DESACTIVADO';
  if (internet?.state === 'invalid_configuration') $('#search-provider').textContent = 'CONFIGURACIÓN INVÁLIDA';
  if (internet?.state === 'paid_search_blocked') $('#search-provider').textContent = 'BRAVE BLOQUEADO / MODO LOCAL';
  const consent = active && internet?.enabled && $('#internet-consent').checked;
  const mode = $('input[name=web-mode]:checked').value;
  $('#web-submit').disabled = !consent || busy || (mode === 'search' && !internet?.search_available);
  $('#network-diagnose').disabled = !consent || busy;
  $('#web-cancel').disabled = !busy;
  $('#network-refresh').disabled = !active;
  $('#cap-network').textContent = active ? consent ? 'Internet' : 'Local' : '—';
}
