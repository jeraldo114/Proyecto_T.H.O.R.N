export function rate(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(2)} MiB/s` : `${(value / 1024).toFixed(1)} KiB/s`;
}

/** Solo ordenes explicitas salen a la red; el modelo no decide destinos. */
export function networkIntent(prompt) {
  const value = prompt.trim().replace(/^thorn[,:]?\s+/i, '');
  const research = /^investiga(?: en (?:internet|la web))?\s+(?:sobre\s+)?(.+)$/iu.exec(value);
  if (research) return { mode: 'search', value: research[1].trim(), investigate: true };
  const search = /^(?:busca|buscar|consulta|investiga)(?:\s+en)?\s+(?:internet|la web|wikipedia)\s+(?:sobre\s+)?(.+)$/iu.exec(value);
  if (search) return { mode: 'search', value: search[1].trim() };
  const read = /^(?:lee|leer|resume|resumir)(?:\s+esta\s+p[aá]gina)?\s+(https:\/\/\S+)$/iu.exec(value);
  return read ? { mode: 'read', value: read[1] } : null;
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
  $('#search-provider').textContent = internet?.provider === 'brave' ? internet.search_available ? 'BRAVE / WEB GENERAL' : 'BRAVE / FALTA API KEY' : internet ? 'WIKIPEDIA / ENCICLOPEDIA' : 'SIN SESIÓN';
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
