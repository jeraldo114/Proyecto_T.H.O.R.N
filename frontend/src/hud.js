import './hud.css';
import 'leaflet/dist/leaflet.css';
import './desktop.css';
import { createIcons, createElement, Mic, Trash2, Play, Orbit, Activity, Terminal, Radio, Database, BrainCircuit, Volume2, Square, ScanEye, Monitor, SlidersHorizontal, Focus, Maximize, KeyRound, ShieldCheck, RefreshCw, Cpu, MemoryStick, HardDrive, ScanLine, Download, Pause, Workflow, LockKeyhole, ArrowUpRight, ArrowUp, ChevronUp, X, PlugZap } from 'lucide';
import { createCoreScene } from './core-scene.js';
import { PcmMicrophone } from './microphone.js';
import { VoicePlayback } from './voice-playback.js';
import { ConversationLoop } from './conversation.js';
import { LazyConversationCapture } from './conversation-loader.js';
import { percent, gib, duration, appendSample, eventMatches, eventLabels } from './model.js';
import { Globe, Search, BookOpen } from 'lucide';
import { networkIntent, renderNetwork, renderSources } from './network.js';
import { SessionClient } from './session.js';
import { OperatorTools } from './operator-tools.js';
import { operatorIntent, localControl } from './workspace.js';
import { assistantState } from './assistant-state.js';
import { HudDesktop } from './desktop.js';

const $ = selector => document.querySelector(selector);
const icons = { Play, Globe, Search, BookOpen, Mic, Orbit, Activity, Terminal, Radio, Database, BrainCircuit, Volume2, Square, ScanEye, Monitor, SlidersHorizontal, Focus, Maximize, KeyRound, ShieldCheck, RefreshCw, Cpu, MemoryStick, HardDrive, ScanLine, Download, Pause, Workflow, LockKeyhole, ArrowUpRight, ArrowUp, ChevronUp, X, PlugZap };
createIcons({ icons });
const consoleSources = document.createElement('div'); consoleSources.id = 'console-sources'; consoleSources.hidden = true;
$('#console-text').after(consoleSources);
const scene = createCoreScene($('#core-canvas'));
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const timeFormat = new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const dateFormat = new Intl.DateTimeFormat('es-CL', { weekday: 'short', day: '2-digit', month: 'long', year: 'numeric' });
$('#timezone').textContent = new Date().toLocaleTimeString('en', { timeZoneName: 'short' }).split(' ').at(-1);
$('#settings-timezone').textContent = timezone;
$('#node-host').textContent = window.location.host;

// El token vive solo en esta clausura: nunca en cookies, URL, storage o informes.
let token = '', status = null, telemetry = null, events = [], history = [], online = false;
let paused = false, inFlight = false, interval = 2000, timer = null, failures = 0;
let lastReceived = 0, animation = true, reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
let lastActivityKey = '', filter = 'all', toastTimer;
let revision = 0, authRevision = 0, statusReceivedAt = 0;
let capabilities = null, pendingAction = null, proposedAction = null, historyBefore = null;
let savedRecords = [], rulesLoaded = false, chatBusy = false, nativeSessionUsed = false;
let speechRevision = 0, speechAbort = null, speechAutomatic = false, speechMessage = '';
let autoVoiceRevision = 0, replyRevision = 0;
let microphone = null, microphoneAbort = null, microphoneRevision = 0, microphoneState = 'idle';
let conversationAutoAttempted = false;
let chatAbort = null, cancelPending = null;
let conversationVoiceSettings = null;
let voiceDefaultKey = '';
const sessions = new SessionClient();
let desktop = null;
let networkData = null, networkReceived = 0, webController = null, webRevision = 0, webReply = false;
const playback = new VoicePlayback(state => {
  const messages = { playing: 'Reproduciendo / ElevenLabs', blocked: 'Audio listo / reproducción bloqueada por el navegador', ended: 'Reproducción terminada', playback_error: 'El dispositivo no pudo reproducir el audio', testing: 'Reproduciendo tono local', test_finished: 'Tono local finalizado', test_blocked: 'El navegador bloqueó la prueba de salida' };
  messages.playing = capabilities?.voice?.cloud ? 'Reproduciendo / ElevenLabs' : 'Reproduciendo / voz local';
  if (messages[state]) setSpeechMessage(messages[state]);
  renderVoice();
  if (conversation.enabled && state === 'blocked') conversation.setState('blocked');
  if (conversation.enabled && state === 'playing') conversation.setState('speaking');
}, level => scene.setVoiceLevel(level));
const conversation = new ConversationLoop({
  createCapture: callbacks => new LazyConversationCapture(callbacks),
  onState: () => { renderConversation(); text('#microphone-result', $('#conversation-state').textContent); renderMicrophone(); renderVoice(); },
  onLevel: ({ level, seconds }) => { scene.setInputLevel(level); $('#microphone-level').value = level; text('#microphone-time', `${seconds.toFixed(1)} / 15 s`); },
  onText: value => { $('#microphone-text').value = value; },
  onInterrupt: () => {
    const generation = authRevision;
    void cancelActiveResponse().then(async () => {
      if (chatBusy) await new Promise(resolve => setTimeout(resolve, 250));
      if (generation === authRevision && !document.hidden && !paused) await startConversation();
    }).catch(() => {});
  },
  transcribe: (pcm, signal) => transcribePcm(pcm, token, signal),
  ask: (prompt, signal) => { conversationVoiceSettings = { automatic: $('#voice-auto').checked, revision: autoVoiceRevision }; return askModel(prompt, signal); },
  speak: async (reply, signal) => {
    if (signal.aborted || !conversationVoiceSettings?.automatic || conversationVoiceSettings.revision !== autoVoiceRevision) return;
    const state = await speakText(reply, { automatic: true });
    if (signal.aborted) return;
    if (state === 'playback_error' || state === 'error') throw new Error('playback_failed');
    if (playback.active && await playback.finished === 'playback_error') throw new Error('playback_failed');
  },
});
const operatorTools = new OperatorTools({
  api, credential: currentCredential, active: () => Boolean(token && online && !paused && !document.hidden),
  navigate: selectView, cancelResponse: cancelActiveResponse,
  canAnnounce: () => !chatBusy && !conversation.enabled,
  canPresent: () => !chatBusy,
  onReply: (result, { announce }) => {
    consoleReply(result.reply);
    if (result.memory_sources?.length) {
      const references = result.memory_sources.map(item => `#${item.id} · ${new Date(item.date).toLocaleString('es-CL')} · ${item.category}`);
      text('#console-text', `${result.reply}\n\nConversaciones consultadas en Historial\n${references.join('\n')}`);
    }
    if (result.memory?.available && !result.memory.saved) toast('La respuesta está lista, pero este turno no se guardó en la memoria permanente.');
    proposedAction = result.suggested_action || null;
    $('#suggested-action-button').hidden = !proposedAction || !capabilities?.actions.enabled;
    if (announce && $('#voice-auto').checked && !conversation.enabled && !chatBusy) void speakText(result.reply, { automatic: true });
  },
  onError: error => toast(serviceError(error)),
  onSources: sources => { webReply = true; renderSources(consoleSources, sources, true); },
});
desktop = new HudDesktop({ tools: operatorTools, scene, onView: selectView, onStop: cancelActiveResponse,
  onSpeak: value => { stopConversation(); return speakText(value, { automatic: false }); },
  credential: currentCredential, active: () => Boolean(token && online && !paused && !document.hidden),
  onError: error => toast(serviceError(error)),
});
$('#motion-setting').checked = reduced;
scene.setMotion(animation, reduced);

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4200);
}
async function currentCredential() {
  const generation = authRevision;
  const value = await sessions.ensure();
  if (generation !== authRevision) throw new Error('session_changed');
  token = value; return value;
}
async function api(path, { method = 'GET', body, credential, timeout = 6000, query = {}, signal } = {}) {
  if (credential === undefined) credential = token ? await currentCredential() : '';
  const headers = {};
  if (credential) headers.Authorization = `Bearer ${credential}`;
  if (body) headers['Content-Type'] = 'application/json';
  const search = new URLSearchParams(query).toString();
  const response = await fetch(`/api/v1/${path}/${search ? `?${search}` : ''}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout), cache: 'no-store', credentials: 'omit',
  });
  const data = await response.json();
  if (!response.ok && !(response.status === 503 && data.sensor)) {
    const error = new Error(data.error || 'backend_unavailable');
    error.status = response.status;
    throw error;
  }
  return data;
}
function fresh() {
  // NOTA DEL OPERADOR: la pantalla conserva contexto, pero marca lo que vencio.
  // No mostramos una muestra vieja como si acabara de llegar del equipo.
  if (!token || !telemetry?.snapshot || !telemetry.sensor.fresh || !online || paused) return false;
  const age = telemetry.sensor.age_seconds + (Date.now() - lastReceived) / 1000;
  return age <= Math.max(5, telemetry.sensor.interval_seconds * 3);
}
function text(selector, value) { $(selector).textContent = value; }
function renderStatus() {
  text('#connection-label', paused ? 'ACTUALIZACIÓN PAUSADA' : online ? 'ENLACE ESTABLECIDO' : 'SIN ENLACE');
  $('#connection-state').classList.toggle('offline', !online || paused);
  text('#runtime-state', online ? status?.runtime.state?.toUpperCase() || '—' : '—');
  text('#uptime', online && status ? duration(status.runtime.uptime_seconds + (performance.now() - statusReceivedAt) / 1000) : '—');
  text('#core-link', online ? 'NÚCLEO EN LÍNEA' : 'NÚCLEO SIN ENLACE');
  text('#core-caption-state', online ? 'NÚCLEO ACTIVO' : 'SIN ENLACE');
  text('#core-caption-detail', paused ? 'MUESTRA RETENIDA' : token ? 'TELEMETRÍA AUTENTICADA' : 'SUPERVISOR ASÍNCRONO');
  text('#session-label', token ? 'Conectado' : 'Conectar');
  $('#session-button').classList.toggle('authenticated', Boolean(token));
  text('#cap-sensor', online && status?.capabilities.system_sensor ? 'Activo' : '—');
  text('#cap-db', online && status ? status.runtime.database_enabled ? 'Activo' : 'Sin DB' : '—');
  const counters = status?.events;
  for (const key of ['published', 'processed', 'queued', 'errors']) text(`#events-${key}`, online && counters ? counters[key === 'errors' ? 'handler_errors' : key].toLocaleString('es-CL') : '—');
  text('#workers', online && counters ? counters.workers : '—');
  text('#bus-live', online && counters?.accepting ? 'OPERATIVO' : '—');
  text('#queue-state', online && counters ? counters.queued ? `${counters.queued} pendientes` : 'Cola libre' : 'Sin lectura');
  text('#footer-mode', token ? 'SESIÓN AUTENTICADA' : 'LECTURA LOCAL');
  text('#footer-sync', paused ? 'ACTUALIZACIÓN PAUSADA' : lastReceived && online ? `ÚLTIMA MUESTRA ${timeFormat.format(new Date(lastReceived))}` : 'SIN MUESTRA PRIVADA');
  text('#activity-mode', paused ? 'PAUSADO' : 'EN VIVO');
  scene.setData({ online, fresh: fresh(), cpu: telemetry?.snapshot?.cpu_percent });
}
function renderTelemetry() {
  const snapshot = telemetry?.snapshot;
  const isFresh = fresh();
  $('.telemetry-column').classList.toggle('stale', Boolean(snapshot) && !isFresh);
  for (const [name, field] of [['cpu', 'cpu_percent'], ['memory', 'memory_percent'], ['disk', 'disk_percent']]) {
    const value = percent(snapshot?.[field]);
    $(`[data-metric=${name}]`).classList.toggle('is-high', value !== null && value >= 90);
    const node = $(`#${name}-value`);
    node.replaceChildren(document.createTextNode(value === null ? '—' : value.toFixed(0)));
    const suffix = document.createElement('small'); suffix.textContent = '%'; node.append(suffix);
    $(`#${name}-bar`).style.width = `${value ?? 0}%`;
  }
  text('#cpu-detail', snapshot ? `${snapshot.logical_cpus ?? '—'} núcleos lógicos` : 'Sin muestra');
  text('#memory-detail', snapshot ? `${gib(snapshot.memory_available_bytes)} disponibles` : 'Sin muestra');
  text('#disk-detail', snapshot ? `${gib(snapshot.disk_free_bytes)} libres` : 'Sin muestra');
  text('#process-count', snapshot ? snapshot.process_count : '—');
  text('#sample-count', telemetry?.sensor.samples ?? '—');
  text('#sample-state', !token ? 'TELEMETRÍA BLOQUEADA' : paused ? 'MUESTRA RETENIDA' : isFresh ? 'MUESTRA VIGENTE' : snapshot ? 'MUESTRA NO VIGENTE' : 'ESPERANDO MUESTRA');
  text('#sample-time', snapshot ? timeFormat.format(new Date(snapshot.sampled_at)) : '—');
  $('#sample-dot').classList.toggle('fresh', isFresh);
  text('#system-fresh-label', isFresh ? 'MUESTRA VIGENTE' : 'SIN MUESTRA VIGENTE');
  text('#system-timestamp', snapshot ? new Date(snapshot.sampled_at).toLocaleString('es-CL') : '—');
  const rows = [
    ['Carga de CPU', snapshot ? `${snapshot.cpu_percent.toFixed(1)} %` : '—'],
    ['Núcleos lógicos', snapshot?.logical_cpus ?? '—'],
    ['Memoria total', gib(snapshot?.memory_total_bytes)],
    ['Memoria disponible', gib(snapshot?.memory_available_bytes)],
    ['Uso de memoria', snapshot ? `${snapshot.memory_percent.toFixed(1)} %` : '—'],
    ['Capacidad del disco del proyecto', gib(snapshot?.disk_total_bytes)],
    ['Espacio libre', gib(snapshot?.disk_free_bytes)],
    ['Uso de disco', snapshot ? `${snapshot.disk_percent.toFixed(1)} %` : '—'],
    ['Procesos activos', snapshot?.process_count ?? '—'],
    ['Errores del sensor', telemetry?.sensor.errors ?? '—'],
    ['Eventos de sensor descartados', telemetry?.sensor.dropped_events ?? '—'],
  ];
  $('#system-table').replaceChildren(...rows.map(([label, value]) => {
    const row = document.createElement('div'); row.className = 'data-row';
    const title = document.createElement('span'); title.textContent = label;
    const content = document.createElement('strong'); content.textContent = value;
    row.append(title, content); return row;
  }));
  drawCharts();
}
function drawChart(canvas, field, color) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(devicePixelRatio, 2);
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  ctx.strokeStyle = '#27353a'; ctx.lineWidth = 0.5;
  for (const y of [rect.height * 0.3, rect.height * 0.75]) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke(); }
  const points = history.filter(item => percent(item[field]) !== null);
  if (!points.length) return;
  const first = Date.parse(points[0].sampled_at), last = Date.parse(points.at(-1).sampled_at);
  const x = point => points.length === 1 ? rect.width : ((Date.parse(point.sampled_at) - first) / Math.max(1, last - first)) * rect.width;
  const y = point => rect.height - 3 - (point[field] / 100) * (rect.height - 6);
  ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(x(point), y(point)) : ctx.moveTo(x(point), y(point)));
  ctx.stroke();
  const point = points.at(-1); ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x(point) - 2, y(point), 2, 0, Math.PI * 2); ctx.fill();
}
function drawCharts() { drawChart($('#cpu-chart'), 'cpu_percent', '#5ad7de'); drawChart($('#memory-chart'), 'memory_percent', '#a5d67d'); }
function renderEvents() {
  const visible = events.filter(event => eventMatches(event, filter));
  const key = `${token ? 'on' : 'off'}:${filter}:${visible.map(event => event.event_id).join(',')}`;
  if (key === lastActivityKey) return;
  lastActivityKey = key;
  if (!visible.length) {
    const item = document.createElement('li'); item.className = 'empty-state'; item.textContent = token ? 'Sin eventos en este filtro' : 'Sesión privada desconectada';
    $('#activity-list').replaceChildren(item);
    const item2 = document.createElement('p'); item2.className = 'empty-state'; item2.textContent = item.textContent;
    $('#event-table').replaceChildren(item2); return;
  }
  // La vista breve agrupa muestras repetidas; el registro conserva todos los eventos.
  const sampleShown = new Set();
  const brief = filter === 'all' ? visible.filter(event => {
    if (!['system.snapshot', 'network.snapshot'].includes(event.event_type)) return true;
    if (sampleShown.has(event.event_type)) return false;
    sampleShown.add(event.event_type); return true;
  }) : visible;
  $('#activity-list').replaceChildren(...brief.slice(0, 8).map(event => {
    const item = document.createElement('li');
    const meta = document.createElement('div'); meta.className = 'event-meta';
    const clock = document.createElement('time'); clock.textContent = timeFormat.format(new Date(event.created_at));
    const tag = document.createElement('span'); tag.className = `event-tag ${eventMatches(event, 'error') ? 'error' : eventMatches(event, 'runtime') ? 'runtime' : ''}`;
    tag.textContent = event.event_type.split('.')[0].toUpperCase(); meta.append(clock, tag);
    const message = document.createElement('div'); message.className = 'event-message'; message.textContent = eventLabels[event.event_type] ?? event.event_type;
    const source = document.createElement('div'); source.className = 'event-source'; source.textContent = event.source;
    item.append(meta, message, source); return item;
  }));
  $('#event-table').replaceChildren(...visible.map(event => {
    const row = document.createElement('div'); row.className = 'event-row';
    for (const value of [timeFormat.format(new Date(event.created_at)), event.event_type, event.source, event.event_id.slice(0, 12)]) {
      const cell = document.createElement('span'); cell.textContent = value; row.append(cell);
    }
    return row;
  }));
}
function schedule() {
  clearTimeout(timer);
  if (!paused && !document.hidden) timer = setTimeout(poll, failures ? Math.min(30000, interval * 2 ** failures) : interval);
}
async function poll() {
  if (inFlight || paused || document.hidden) return;
  inFlight = true;
  const requestRevision = revision;
  try {
    const started = performance.now();
    const nextStatus = await api('status', { credential: '' });
    if (requestRevision !== revision) return;
    status = nextStatus; statusReceivedAt = performance.now();
    text('#api-latency', `${Math.round(performance.now() - started)} ms`);
    online = status.runtime.state === 'running'; failures = 0;
    if (token) {
      const credential = await currentCredential();
      const [next, activity, modules, network] = await Promise.allSettled([api('system', { credential }), api('activity', { credential }), api('capabilities', { credential }), api('network', { credential })]);
      if (requestRevision !== revision) return;
      if ([next, activity, modules, network].some(result => result.status === 'rejected' && result.reason.status === 401)) {
        disconnect(); toast('Sesión no válida. Vuelve a conectar.'); return;
      }
      if (next.status === 'fulfilled') {
        telemetry = next.value; lastReceived = Date.now();
        if (telemetry.sensor.fresh) history = appendSample(history, telemetry.snapshot);
      } else { if (telemetry) telemetry.sensor.fresh = false; }
      if (activity.status === 'fulfilled') events = activity.value.events;
      capabilities = modules.status === 'fulfilled' ? modules.value : null;
      if (network.status === 'fulfilled') { networkData = network.value; networkReceived = Date.now(); }
    }
  } catch (error) {
    if (error.status === 401 && requestRevision === revision) disconnect();
    online = false; failures = Math.min(failures + 1, 4);
    text('#api-latency', '—');
  } finally {
    inFlight = false; renderStatus(); renderTelemetry(); renderEvents(); renderServices(); schedule();
  }
}
function disconnect() {
  revision++; authRevision++;
  chatAbort?.abort(); Object.keys(timings).forEach(key => delete timings[key]); operatorTools.disconnect();
  sessions.close(); voiceDefaultKey = '';
  cancelWeb(); $('#internet-consent').checked = false; networkData = null; networkReceived = 0;
  $('#web-input').value = ''; $('#web-sources').replaceChildren(); text('#web-result', ''); text('#network-probe-result', 'SIN PRUEBA EXTERNA');
  stopConversation(); conversationAutoAttempted = false;
  cancelMicrophone(); $('#microphone-text').value = ''; resetMicrophoneDevices();
  autoVoiceRevision++; replyRevision++;
  stopSpeech(); $('#voice-consent').checked = false; $('#voice-auto').checked = false; $('#voice-text').value = ''; setSpeechMessage('');
  token = ''; telemetry = null; events = []; history = []; lastReceived = 0;
  capabilities = null; pendingAction = null; proposedAction = null; savedRecords = []; rulesLoaded = false;
  $('#action-dialog').close(); $('#suggested-action-button').hidden = true;
  $('#alert-rules').replaceChildren(); $('#history-table').replaceChildren(); $('#more-history-button').hidden = true;
  $('#memory-input').value = ''; $('#memory-list').replaceChildren(); text('#memory-state', ''); text('#memory-count', '—'); text('#action-result', ''); text('#alert-save-state', '');
  $('#token-input').value = ''; $('#console-text').textContent = 'Sesión desconectada';
  renderStatus(); renderTelemetry(); renderEvents(); renderServices();
}
$('#session-button').addEventListener('click', () => { cancelWeb(); $('#internet-consent').checked = false; });
$('#session-button').addEventListener('click', () => { stopConversation(); authRevision++; autoVoiceRevision++; stopSpeech(); $('#voice-consent').checked = false; $('#voice-auto').checked = false; cancelMicrophone(); $('#session-error').textContent = ''; $('#session-dialog').showModal(); });
$('#close-session').addEventListener('click', () => { $('#session-dialog').close(); $('#token-input').value = ''; });
$('#session-dialog').addEventListener('close', () => { $('#token-input').value = ''; });
$('#disconnect-button').addEventListener('click', () => { disconnect(); $('#session-dialog').close(); toast('Sesión privada desconectada'); });
$('#session-form').addEventListener('submit', async event => {
  event.preventDefault();
  const candidate = $('#token-input').value.trim();
  const requestRevision = ++authRevision;
  const button = event.submitter; button.disabled = true;
  try {
    const issued = await sessions.exchange(candidate);
    if (requestRevision !== authRevision || !$('#session-dialog').open) { sessions.revoke(issued.access_token); return; }
    sessions.accept(issued);
    revision++;
    token = sessions.token; conversationAutoAttempted = false; $('#token-input').value = ''; $('#session-dialog').close();
    toast('Sesión autenticada'); await poll();
  } catch (error) {
    text('#session-error', error.status === 401 ? 'Token de acceso incorrecto' : 'El núcleo no está disponible');
    $('#token-input').value = '';
  } finally { button.disabled = false; }
});
function selectView(name) {
  if (name !== 'assistant' && !conversation.enabled) cancelMicrophone();
  for (const view of document.querySelectorAll('.view')) view.hidden = view.id !== `view-${name}`;
  for (const nav of document.querySelectorAll('[data-view]')) {
    nav.classList.toggle('active', nav.dataset.view === name);
    if (nav.dataset.view === name) nav.setAttribute('aria-current', 'page'); else nav.removeAttribute('aria-current');
  }
  text('#page-title', { central: 'Centro de mando', system: 'Sistema', activity: 'Actividad', assistant: 'Asistente', history: 'Historial', network: 'Red e Internet' }[name]);
  window.dispatchEvent(new CustomEvent('thorn-view-change', { detail: name }));
  if (name === 'history') { loadHistory(); loadMemory(); }
  if (name === 'assistant' && token) loadRules();
  drawCharts();
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => selectView(button.dataset.view)));
$('#all-activity-button').addEventListener('click', () => selectView('activity'));
$('#activity-filter').addEventListener('change', event => { filter = event.target.value; renderEvents(); });
$('#refresh-button').addEventListener('click', () => { if (paused) toast('La actualización está pausada'); else poll(); });
$('#pause-button').addEventListener('click', () => {
  paused = !paused; revision++;
  if (paused) cancelWeb();
  if (paused) stopConversation();
  autoVoiceRevision++; if (paused) stopSpeech();
  document.body.classList.toggle('paused', paused);
  $('#pause-button').replaceChildren(createElement(paused ? Play : Pause));
  $('#pause-button').setAttribute('aria-pressed', String(paused));
  $('#pause-button').setAttribute('aria-label', paused ? 'Reanudar actualización' : 'Pausar actualización');
  $('#pause-button').dataset.tooltip = paused ? 'Reanudar actualización' : 'Pausar actualización';
  clearTimeout(timer); renderStatus(); renderTelemetry(); renderServices(); if (!paused) poll();
});
$('#focus-button').addEventListener('click', () => desktop.setMode(desktop.mode === 'presence' ? 'work' : 'presence'));
$('#fullscreen-button').addEventListener('click', async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
  catch { toast('Pantalla completa no disponible'); }
});
function setMotion() {
  scene.setMotion(animation, reduced); $('#animation-setting').checked = animation;
  $('#animation-button').setAttribute('aria-pressed', String(!animation));
  $('#animation-button').replaceChildren(createElement(animation ? Pause : Play));
  $('#animation-button').dataset.tooltip = animation ? 'Pausar animación' : 'Reanudar animación';
  $('#animation-button').setAttribute('aria-label', $('#animation-button').dataset.tooltip);
}
$('#animation-button').addEventListener('click', () => { animation = !animation; setMotion(); });
$('#animation-setting').addEventListener('change', event => { animation = event.target.checked; setMotion(); });
$('#motion-setting').addEventListener('change', event => { reduced = event.target.checked; setMotion(); });
$('#settings-button').addEventListener('click', () => $('#settings-dialog').showModal());
$('#mobile-settings-button').addEventListener('click', () => $('#settings-dialog').showModal());
$('#close-settings').addEventListener('click', () => $('#settings-dialog').close());
$('#poll-interval').addEventListener('change', event => { interval = Number(event.target.value); schedule(); });
function download(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `${name}-${new Date().toISOString().replaceAll(':', '-')}.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function report() {
  if (!token || !telemetry?.snapshot) { toast('No hay muestras privadas para exportar'); return; }
  download({ generated_at: new Date().toISOString(), source: 'T.H.O.R.N Bastion 9', fresh: fresh(), status, telemetry, observed_history: history, events }, 'thorn-informe');
  toast('Informe exportado');
}
$('#report-button').addEventListener('click', report); $('#system-report-button').addEventListener('click', report);
$('#export-events-button').addEventListener('click', () => { if (!token) { toast('Conecta la sesión privada'); return; } download({ generated_at: new Date().toISOString(), durable: false, events: events.filter(event => eventMatches(event, filter)) }, 'thorn-actividad'); });
function consoleReply(message) { replyRevision++; webReply = false; consoleSources.replaceChildren(); consoleSources.hidden = true; stopSpeech(); setSpeechMessage(''); text('#console-text', message); $('#console-output').hidden = false; $('#console-toggle').setAttribute('aria-expanded', 'true'); renderVoice(); }
function serviceError(error) {
  const operatorErrors = { interaction_busy: 'Hay una operación en curso en esta sesión.', interaction_cancelled: 'Operación cancelada.',
    document_busy: 'El lector PDF está ocupado.', document_invalid_pdf: 'El archivo no es un PDF compatible.', document_not_found: 'Selecciona un documento de esta sesión.',
    document_timeout: 'El PDF superó el tiempo de lectura.', document_memory_limit: 'El PDF superó el límite de memoria.',
    document_encrypted: 'Este lector no abre PDF protegidos con contraseña.', document_page_limit: 'El PDF supera las 40 páginas.',
    document_size_limit: 'Selecciona un PDF de hasta 8 MiB.', document_session_limit: 'Cierra un documento antes de abrir otro (máximo 2).',
    document_storage_limit: 'La memoria temporal de documentos está llena.', camera_consent_required: 'Marca la autorización de cámara antes de iniciarla.',
    camera_not_started: 'Inicia la cámara local antes de activar los gestos.', insufficient_scope: 'Esta sesión no tiene el permiso necesario.',
    invalid_preferences: 'Revisa las preferencias y el nombre (hasta 60 caracteres).', invalid_timezone: 'La zona horaria no es válida.' };
  if (operatorErrors[error.message]) return operatorErrors[error.message];
  const webErrors = { web_internet_consent_required: 'Autoriza Internet en Red antes de consultar fuentes externas.', web_disabled: 'Internet está desactivado en el servidor.', web_closed: 'El servicio de Internet está cerrado.', web_busy: 'Hay una consulta web en curso.', web_rate_limited: 'Límite de consultas web. Espera antes de repetir.', web_invalid_query: 'La búsqueda admite hasta 240 caracteres.', web_invalid_url: 'La URL no es válida.', web_public_https_required: 'Solo se permiten URLs HTTPS públicas, sin credenciales ni puertos alternativos.', web_unsupported_content: 'La página no ofrece HTML o texto compatible.', web_redirect_rejected: 'La redirección no está permitida.', web_response_too_large: 'La página supera el límite de 1 MiB.', web_missing_search_key: 'Falta la API key de búsqueda Brave en el servidor.', web_empty_content: 'No se encontró texto legible en esa página.', web_provider_unavailable: 'No se pudo consultar la fuente. Puede estar bloqueada, requerir acceso o no responder.', web_invalid_response: 'La fuente no entregó datos válidos.' };
  if (webErrors[error.message]) return webErrors[error.message];
  if (error.message === 'action_outcome_unrecorded') return 'Windows aceptó la apertura, pero no se pudo completar la auditoría. Comprueba la ventana antes de repetir.';
  return ({ engine_unavailable: 'El motor local Ollama no está disponible.', model_missing: 'El modelo de texto no está instalado en Ollama.', model_busy: 'El modelo está ocupado. Espera la consulta actual.', model_query_failed: 'El modelo no entregó una respuesta válida dentro del plazo.', database_required: 'Esta operación requiere MySQL.', actions_require_windows_and_database: 'Las acciones requieren Windows y auditoría MySQL.', confirmation_expired_or_consumed: 'La confirmación caducó o ya fue utilizada.', action_busy: 'Hay una acción en curso.', action_failed: 'Windows no aceptó la apertura.', unauthorized: 'La sesión privada no es válida.' })[error.message] || 'Servicio no disponible. Comprueba el enlace y la sesión.';
}
function renderServices() {
  const data = token && online && !paused ? capabilities : null;
  operatorTools.render(data);
  const language = data?.language;
  const states = { ready: 'DISPONIBLE', model_missing: 'SIN MODELO', engine_unavailable: 'SIN MOTOR', unchecked: 'SIN COMPROBAR' };
  text('#cap-language', language?.available ? 'Activo' : language?.state === 'engine_unavailable' ? 'Sin motor' : language ? 'Sin modelo' : '—');
  text('#cap-actions', data?.actions.enabled ? 'Listo' : data ? 'Bloqueado' : '—');
  text('#model-state', language?.busy ? 'CONSULTANDO' : states[language?.state] || 'SIN LECTURA');
  text('#model-name', language?.model || '—'); text('#audit-state', data ? data.audit.enabled ? 'MySQL' : 'Sin DB' : '—');
  const catalog = data?.actions.catalog || [];
  const select = $('#action-select'), old = select.value;
  if (select.dataset.catalog !== JSON.stringify(catalog)) {
    select.dataset.catalog = JSON.stringify(catalog);
    select.replaceChildren(...(catalog.length ? catalog : [{ id: '', label: 'Sin acciones disponibles' }]).map(item => {
      const option = document.createElement('option'); option.value = item.id; option.textContent = item.label; return option;
    }));
    if (catalog.some(item => item.id === old)) select.value = old;
  }
  select.disabled = !catalog.length; $('#prepare-action-button').disabled = !catalog.length;
  if (data && !data.actions.enabled) text('#action-result', data.actions.requires);
  $('#save-alerts-button').disabled = !data?.audit.enabled || !rulesLoaded;
  const active = data?.alerts.active || [];
  const labels = { cpu: 'CPU', memory: 'RAM', disk: 'Disco' };
  const descriptions = active.map(item => `${labels[item.resource] || item.resource}: ${Number(item.percent).toFixed(1)} %`);
  text('#active-alerts', data ? descriptions.join(' · ') || (fresh() ? 'Sin alertas activas' : 'Sin lectura vigente') : 'Sin sesión vigente');
  text('#alert-state', data ? `${active.length} ACTIVAS / ${data.alerts.errors} ERRORES` : '—');
  $('#alert-banner').hidden = !active.length;
  text('#alert-banner', `${fresh() ? 'Presión del sistema' : 'Última alerta observada'} · ${descriptions.join(' · ')}`);
  renderVoice();
  renderMicrophone();
  renderConversation();
  updateNetwork();
  if (!conversationAutoAttempted && token && data?.microphone.available) {
    conversationAutoAttempted = true;
    if (data.conversation?.auto_start) void startConversation();
  }
}

function updateNetwork() {
  const data = networkData ? { ...networkData, sensor: { ...networkData.sensor, fresh: networkData.sensor.fresh && Date.now() - networkReceived < 6000 } } : null;
  renderNetwork(data, { active: Boolean(token && online && !paused), busy: chatBusy || Boolean(webController) });
}
function cancelWeb() {
  const active = Boolean(webController);
  webRevision++; webController?.abort(); webController = null;
  if (active || webReply) {
    stopConversation(); autoVoiceRevision++; stopSpeech();
    if (active) { replyRevision++; text('#console-text', 'Consulta web cancelada'); text('#web-result', 'Consulta cancelada'); }
    consoleSources.replaceChildren(); consoleSources.hidden = true;
    $('#web-sources').replaceChildren(); $('#web-sources').hidden = true; webReply = false;
  }
  updateNetwork();
}
$('#internet-consent').addEventListener('change', () => { if (!$('#internet-consent').checked) cancelWeb(); updateNetwork(); });
$('#web-cancel').addEventListener('click', cancelWeb);
$('#network-refresh').addEventListener('click', () => poll());
document.querySelectorAll('input[name=web-mode]').forEach(input => input.addEventListener('change', () => {
  const read = $('input[name=web-mode]:checked').value === 'read';
  $('#web-input').type = read ? 'url' : 'text'; $('#web-input').maxLength = read ? 2048 : 240;
  text('#web-input-label', read ? 'URL HTTPS pública' : 'Consulta'); updateNetwork();
}));
async function submitWeb(intent) {
  stopConversation();
  const automatic = $('#voice-auto').checked, autoRevision = autoVoiceRevision;
  try {
    const reply = await askModel(intent.value || '', undefined, intent);
    if (reply && automatic && autoRevision === autoVoiceRevision) void speakText(reply, { automatic: true });
  } catch (error) { text('#web-result', error.name === 'AbortError' ? 'Consulta cancelada' : serviceError(error)); }
}
$('#web-form').addEventListener('submit', event => { event.preventDefault(); void submitWeb({ mode: $('input[name=web-mode]:checked').value, value: $('#web-input').value.trim() }); });
$('#network-diagnose').addEventListener('click', () => void submitWeb({ mode: 'diagnose' }));

function renderConversation() {
  const states = { off: 'Micrófono apagado', loading: 'Preparando escucha local…', listening: 'Escuchando / local', hearing: 'Recibiendo tu voz / local', transcribing: 'Micrófono cerrado / transcribiendo', thinking: 'Micrófono cerrado / consultando', speaking: 'Micrófono cerrado / respuesta', blocked: 'Audio pendiente / pulsa reproducir', permission_required: 'Permiso o gesto inicial pendiente / activa Conversación', device_lost: 'Micrófono desconectado', input_error: 'No se pudo iniciar la escucha', turn_error: 'Conversación detenida / servicio no disponible' };
  states.waiting_for_wake = 'Escucha local / di «Thorn» para empezar';
  $('#conversation-enabled').checked = conversation.enabled;
  $('#conversation-enabled').disabled = !token || !online || paused || !capabilities?.microphone.available || !PcmMicrophone.supported();
  $('#conversation-stop').disabled = !conversation.enabled;
  $('#conversation-wake').disabled = conversation.enabled;
  $('#conversation-barge').disabled = conversation.enabled || !navigator.mediaDevices?.getSupportedConstraints?.().echoCancellation;
  if (conversation.interruptOnly) { states.speaking = 'Respuesta / escucha de interrupción local'; states.thinking = 'Consultando / escucha de interrupción local'; }
  $('#conversation-bar').dataset.state = conversation.state;
  text('#conversation-state', states[conversation.state] || states.off);
  if (conversation.enabled) text('#microphone-result', states[conversation.state] || states.off);
  if (conversation.enabled && (!token || !online || paused || !capabilities?.microphone.available)) stopConversation();
}
function stopConversation() {
  if (!conversation.enabled) return;
  const outputActive = Boolean(speechAbort || playback.active);
  conversation.stop();
  autoVoiceRevision++; stopSpeech();
  if (outputActive) setSpeechMessage('Conversación detenida');
}
async function startConversation() {
  if (!token || !online || paused || document.hidden || chatBusy || !capabilities?.microphone.available || !PcmMicrophone.supported()) return;
  conversationAutoAttempted = true;
  cancelMicrophone(); stopSpeech();
  conversation.requireWake = $('#conversation-wake').checked;
  conversation.bargeIn = $('#conversation-barge').checked && Boolean(navigator.mediaDevices.getSupportedConstraints?.().echoCancellation);
  await conversation.start($('#microphone-device').value);
}
$('#conversation-enabled').addEventListener('change', event => { conversationAutoAttempted = true; if (event.target.checked) void startConversation(); else stopConversation(); });
$('#conversation-stop').addEventListener('click', stopConversation);

async function transcribePcm(pcm, credential, signal) {
  credential = await currentCredential();
  if (signal.aborted) throw new Error('cancelled');
  const response = await fetch('/api/v1/microphone/transcribe/', {
    method: 'POST', headers: { Authorization: `Bearer ${credential}`, 'Content-Type': 'application/octet-stream', 'X-Thorn-Microphone-Consent': 'local' },
    body: pcm, cache: 'no-store', credentials: 'omit', signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'microphone_transcription_failed');
  if (typeof result.text !== 'string' || result.text.length > 2000 || result.cloud !== false) throw new Error('microphone_transcription_failed');
  timings.transcription = result.transcription_ms;
  return result.text;
}

const timings = {};
async function streamChat(prompt, signal, onDelta) {
  const credential = await currentCredential();
  const response = await fetch('/api/v1/chat/', {
    method: 'POST', headers: { Authorization: `Bearer ${credential}`, 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify({ prompt }), cache: 'no-store', credentials: 'omit',
    signal: AbortSignal.any([signal, AbortSignal.timeout(70000)]),
  });
  if (!response.ok) throw new Error((await response.json()).error || 'service_unavailable');
  if (!response.headers.get('content-type')?.startsWith('application/x-ndjson')) return response.json();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error('model_query_failed');
      pending += value;
      if (pending.length > 262144) throw new Error('model_query_failed');
      let end;
      while ((end = pending.indexOf('\n')) >= 0) {
        const event = JSON.parse(pending.slice(0, end));
        pending = pending.slice(end + 1);
        if (event.error) throw new Error(event.error);
        if (event.result) { timings.generation = event.result.elapsed_ms; return event.result; }
        if (typeof event.delta === 'string') onDelta(event.delta);
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

async function askModel(prompt, signal, intent = operatorIntent(prompt) || networkIntent(prompt)) {
  if (cancelPending) await cancelPending.catch(() => {});
  if (!token || chatBusy) throw new Error('model_busy');
  if (intent && !$('#internet-consent').checked) { const error = new Error('web_internet_consent_required'); consoleReply(serviceError(error)); throw error; }
  chatBusy = true; proposedAction = null; $('#suggested-action-button').hidden = true;
  const credential = token, generation = authRevision;
  consoleReply(intent ? 'Consultando fuentes de Internet…' : 'Consultando al modelo local…');
  const internetGeneration = webRevision;
  const controller = intent ? new AbortController() : null;
  const requestController = new AbortController(); chatAbort = requestController;
  const activeSignal = AbortSignal.any([requestController.signal, ...(signal ? [signal] : []), ...(controller ? [controller.signal] : [])]);
  if (controller) { webController = controller; text('#web-result', 'CONSULTANDO / INTERNET'); updateNetwork(); }
  const outputRevision = replyRevision;
  try {
    const body = intent ? { internet_consent: true, ...(['search', 'map'].includes(intent.mode) ? { query: intent.value, ...(intent.investigate ? { investigate: true } : {}) } : intent.mode === 'read' ? { url: intent.value } : {}) } : { prompt };
    const route = intent ? intent.mode === 'map' ? 'maps/lookup' : intent.mode === 'diagnose' ? 'network/diagnose' : `web/${intent.mode}` : 'chat';
    let partial = '', spokenUntil = 0, audio = Promise.resolve();
    const voiceRevision = autoVoiceRevision;
    const auto = $('#voice-auto').checked && capabilities?.voice.can_request && !capabilities.voice.cloud && playback.volume > 0;
    const queueSpeech = value => {
      audio = audio.then(async () => {
        if (activeSignal.aborted || generation !== authRevision || voiceRevision !== autoVoiceRevision) return;
        await speakText(value, { automatic: true });
        if (playback.state === 'blocked') { stopSpeech(); setSpeechMessage('Audio bloqueado. Pulsa el botón de lectura para oír la respuesta.'); }
        else if (playback.active) await playback.finished;
      });
    };
    const reply = intent ? await api(route, { method: 'POST', body, timeout: 90000, signal: activeSignal })
      : await streamChat(prompt, activeSignal, delta => {
        if (activeSignal.aborted || generation !== authRevision || outputRevision !== replyRevision) return;
        partial += delta;
        text('#console-text', partial);
        if (!auto) return;
        const sentence = /^(.*?[.!?])\s+/su.exec(partial.slice(spokenUntil));
        if (sentence && sentence[1].trim().length >= 16) {
          queueSpeech(sentence[1].trim()); spokenUntil += sentence[0].length;
        }
      });
    if (spokenUntil) {
      const tail = reply.reply.slice(spokenUntil).trim();
      if (tail) queueSpeech(tail);
      await audio;
    }
    if (activeSignal?.aborted || generation !== authRevision || outputRevision !== replyRevision || intent && internetGeneration !== webRevision) return '';
    if (intent?.mode === 'diagnose') {
      reply.reply = `Prueba HTTPS a example.com completada.\nDNS: ${reply.dns_ms} ms\nSolicitud HTTPS: ${reply.https_ms} ms\nMedido: ${new Date(reply.checked_at).toLocaleString('es-CL')}\nEs una prueba de ese destino, no una medición ICMP ni de todo Internet.`;
      text('#network-probe-result', `DNS ${reply.dns_ms} ms / HTTPS ${reply.https_ms} ms`);
    }
    const shouldSpeak = operatorTools.accept(reply, { announce: false });
    if (intent) {
      webReply = true; renderSources(consoleSources, reply.sources, true); renderSources($('#web-sources'), reply.sources);
      text('#web-result', intent.mode === 'map' ? 'COORDENADAS CON FUENTE / MAPA EXTERNO' : intent.mode === 'diagnose' ? 'PRUEBA COMPLETADA' : reply.summary_method === 'local_llm' ? 'RESUMEN LOCAL / FUENTES EXTERNAS' : 'EXTRACTOS / FUENTES EXTERNAS');
    }
    proposedAction = reply.suggested_action;
    $('#suggested-action-button').hidden = !proposedAction || !capabilities?.actions.enabled;
    return shouldSpeak && !spokenUntil ? reply.reply : '';
  } catch (error) {
    const wasAborted = activeSignal.aborted;
    requestController.abort();
    if (!wasAborted && generation === authRevision && outputRevision === replyRevision) { consoleReply(serviceError(error)); if (intent) text('#web-result', serviceError(error)); }
    throw error;
  } finally { if (chatAbort === requestController) chatAbort = null; if (webController === controller) webController = null; chatBusy = false; updateNetwork(); renderDesktop(); }
}

function cancelActiveResponse() {
  stopConversation(); stopSpeech(); chatAbort?.abort(); autoVoiceRevision++; replyRevision++;
  if (!token || cancelPending) return cancelPending || Promise.resolve();
  const task = api('conversation/cancel', { method: 'POST', body: {}, timeout: 5000 });
  cancelPending = task;
  void task.catch(() => {}).finally(() => { if (cancelPending === task) cancelPending = null; });
  return task;
}
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && (chatBusy || playback.active || conversation.enabled)) void cancelActiveResponse().catch(() => {});
});

const voiceStates = { disabled: 'Desactivada', missing_key: 'Falta API key', missing_voice: 'Falta voz', configured: 'Configurada / sin probar', ready: 'Síntesis verificada', invalid_configuration: 'Configuración inválida', auth_failed: 'Credenciales rechazadas', not_found: 'Voz no encontrada', rate_limited: 'Límite del proveedor', provider_unavailable: 'Proveedor sin enlace', invalid_audio: 'Audio no válido', closed: 'Servicio cerrado' };
function renderVoice() {
  const voice = token && online && !paused ? capabilities?.voice : null;
  const key = voice ? `${authRevision}:${voice.provider}:${Boolean(voice.cloud)}` : '';
  if (key && key !== voiceDefaultKey) {
    voiceDefaultKey = key; $('#voice-consent').checked = false;
    $('#voice-auto').checked = !voice.cloud && Boolean(voice.can_request);
  }
  const permitted = voice?.can_request && (!voice.cloud || $('#voice-consent').checked);
  if (!permitted && (speechAbort || playback.audio)) stopSpeech();
  const busy = Boolean(speechAbort || playback.active);
  const localStates = { ready: 'Piper / local', model_missing: 'Falta modelo local', engine_missing: 'Falta motor local', model_invalid: 'Modelo local inválido', loading: 'Cargando voz local', policy_blocked: 'Proveedor bloqueado por modo local' };
  text('#voice-state', (!voice?.cloud && localStates[voice?.state]) || voiceStates[voice?.state] || 'Sin sesión');
  text('#voice-service-label', voice?.cloud ? 'VOZ / ELEVENLABS' : 'VOZ / LOCAL');
  text('#cap-voice-label', voice?.cloud ? 'Voz / nube' : 'Voz / local');
  text('#ai-policy', `${capabilities?.identity?.local_ai_only ? 'IA Y VOZ LOCALES · SIN CRÉDITOS' : 'PROVEEDORES SEGÚN CONFIGURACIÓN'}${capabilities?.storage?.encrypted ? ' · MEMORIA PROTEGIDA' : ''}`);
  $('#voice-consent').closest('label').hidden = !voice?.cloud;
  text('#cap-voice', voice?.state === 'ready' ? 'Activa' : voice?.can_request ? 'Lista' : voice ? 'Sin activar' : '—');
  text('#voice-model', voice?.model || '—');
  const enabled = permitted && !voice.busy && !busy && playback.volume > 0 && microphoneState === 'idle' && !conversation.enabled;
  $('#voice-auto').disabled = !permitted;
  $('#speaker-test-button').disabled = busy || microphoneState !== 'idle' || conversation.enabled;
  for (const id of ['#voice-resume-button', '#resume-reply-button']) {
    $(id).hidden = playback.state !== 'blocked';
    $(id).disabled = !permitted || microphoneState !== 'idle';
  }
  $('#voice-test-button').disabled = !enabled;
  $('#speak-reply-button').disabled = !enabled || !$('#console-text').textContent.trim() || $('#console-text').textContent.length > (voice?.max_characters || 2000);
  $('#voice-stop-button').disabled = !busy; $('#stop-reply-button').disabled = !busy;
  text('#reply-voice-result', speechMessage || (!voice ? 'Voz: sin sesión' : !permitted ? 'Voz: sin habilitar' : $('#voice-auto').checked ? 'Voz: lectura automática' : 'Voz: lectura manual'));
  renderDesktop();
}
function setSpeechMessage(message) { speechMessage = message; text('#voice-result', message); text('#reply-voice-result', message); }
function stopSpeech() {
  speechRevision++;
  speechAbort?.abort(); speechAbort = null;
  speechAutomatic = false;
  playback.stop();
  renderVoice();
}
async function speakText(value, { automatic = false } = {}) {
  if (!token || !online || paused || !capabilities?.voice.can_request || (capabilities.voice.cloud && !$('#voice-consent').checked) || microphoneState !== 'idle') return;
  if (automatic && (document.hidden || !$('#voice-auto').checked || speechAbort || playback.active)) return;
  if (playback.volume === 0) { setSpeechMessage('Voz silenciada / volumen 0 %'); return; }
  if (!value.trim() || value.length > capabilities.voice.max_characters) { setSpeechMessage('El texto supera el límite de voz / máximo 2000 caracteres'); return; }
  if (!automatic) { stopConversation(); autoVoiceRevision++; }
  stopSpeech();
  speechAutomatic = automatic;
  const generation = speechRevision, session = authRevision, voice = capabilities.voice;
  const controller = new AbortController(); speechAbort = controller;
  setSpeechMessage(voice.cloud ? 'Sintetizando con ElevenLabs…' : 'Sintetizando en tu equipo…'); renderVoice();
  try {
    const credential = await currentCredential();
    if (controller.signal.aborted || session !== authRevision) return;
    const response = await fetch('/api/v1/voice/speech/', {
      method: 'POST', headers: { Authorization: `Bearer ${credential}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: value, cloud_consent: Boolean(voice.cloud && $('#voice-consent').checked) }), cache: 'no-store', credentials: 'omit',
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(55000)]),
    });
    if (!response.ok) throw new Error((await response.json()).error || 'voice_provider_unavailable');
    if (!response.headers.get('content-type')?.startsWith(voice.cloud ? 'audio/mpeg' : 'audio/wav')) throw new Error('voice_invalid_audio');
    timings.synthesis = Number(response.headers.get('X-Thorn-Synthesis-Ms') ?? NaN);
    const blob = await response.blob();
    if (generation !== speechRevision || session !== authRevision) return;
    if (!blob.size || blob.size > (voice.cloud ? 2 : 8) * 1024 * 1024) throw new Error('voice_invalid_audio');
    speechAbort = null;
    await playback.start(blob);
    return playback.state;
  } catch (error) {
    if (generation !== speechRevision || session !== authRevision) return;
    stopSpeech();
    setSpeechMessage(({ voice_auth_failed: 'API key sin permiso de síntesis. Revisa el .env privado.', voice_not_found: 'La voz configurada no está disponible en tu cuenta.', voice_rate_limited: 'ElevenLabs ha limitado la solicitud. Revisa los créditos de tu cuenta.', voice_busy: 'Hay una síntesis en curso. Espera antes de repetir.', voice_invalid_audio: 'El proveedor no entregó audio válido.' })[error.message] || 'No se pudo completar la síntesis. No se reintenta automáticamente.');
    return 'error';
  }
}
$('#voice-form').addEventListener('submit', event => { event.preventDefault(); speakText($('#voice-text').value); });
$('#speak-reply-button').addEventListener('click', () => speakText($('#console-text').textContent));
for (const id of ['#voice-stop-button', '#stop-reply-button']) $(id).addEventListener('click', () => { stopConversation(); autoVoiceRevision++; stopSpeech(); setSpeechMessage('Detenido'); });
for (const id of ['#voice-resume-button', '#resume-reply-button']) $(id).addEventListener('click', () => { if (token && online && !paused && (!capabilities?.voice.cloud || $('#voice-consent').checked)) void playback.resume(); });
$('#voice-consent').addEventListener('change', () => { autoVoiceRevision++; if (!$('#voice-consent').checked) { stopConversation(); $('#voice-auto').checked = false; stopSpeech(); setSpeechMessage('Autorización de voz retirada'); } renderVoice(); });
$('#voice-auto').addEventListener('change', () => { autoVoiceRevision++; if (!$('#voice-auto').checked && speechAutomatic) { stopSpeech(); setSpeechMessage('Lectura automática detenida'); } renderVoice(); });
$('#voice-volume').addEventListener('input', event => { const value = Number(event.target.value); playback.setVolume(value / 100); text('#voice-volume-value', `${value} %`); if (!value) setSpeechMessage('Voz silenciada / volumen 0 %'); else if (speechMessage === 'Voz silenciada / volumen 0 %') setSpeechMessage(''); renderVoice(); });
$('#speaker-test-button').addEventListener('click', () => { autoVoiceRevision++; stopSpeech(); speechAutomatic = false; void playback.testOutput(); });
$('#close-console').addEventListener('click', () => { stopConversation(); replyRevision++; autoVoiceRevision++; stopSpeech(); });

function resetMicrophoneDevices() {
  const option = document.createElement('option'); option.value = ''; option.textContent = 'Micrófono predeterminado';
  $('#microphone-device').replaceChildren(option);
}
function renderMicrophone() {
  const input = token && online && !paused ? capabilities?.microphone : null;
  const ready = Boolean(input?.available && PcmMicrophone.supported());
  if (!ready && microphoneState !== 'idle') cancelMicrophone('Dictado detenido / sin enlace');
  const states = { ready: 'VOSK / LOCAL', disabled: 'DESACTIVADO', model_missing: 'SIN MODELO', engine_missing: 'SIN MOTOR', loading: 'CARGANDO', model_invalid: 'MODELO INVÁLIDO', invalid_configuration: 'CONFIGURACIÓN INVÁLIDA', closed: 'SERVICIO CERRADO' };
  text('#microphone-engine', !PcmMicrophone.supported() ? 'DISPOSITIVO NO COMPATIBLE' : states[input?.state] || 'SIN SESIÓN');
  $('#microphone-start').disabled = !ready || input.busy || microphoneState !== 'idle' || conversation.enabled;
  $('#console-microphone').disabled = $('#microphone-start').disabled;
  $('#microphone-start').setAttribute('aria-pressed', String(microphoneState === 'listening'));
  $('#microphone-device').disabled = microphoneState !== 'idle' || conversation.enabled;
  $('#microphone-finish').disabled = microphoneState !== 'listening';
  $('#microphone-cancel').disabled = microphoneState === 'idle';
  $('#microphone-send').disabled = !token || !online || paused || conversation.enabled || microphoneState !== 'idle' || !$('#microphone-text').value.trim();
}
function cancelMicrophone(message = 'Micrófono apagado') {
  microphoneRevision++; microphone?.cancel(); microphone = null;
  microphoneAbort?.abort(); microphoneAbort = null; microphoneState = 'idle';
  $('#microphone-level').value = 0; text('#microphone-time', '0.0 / 15 s'); text('#microphone-result', message);
}
async function startMicrophone() {
  if (!token || !online || paused || conversation.enabled || microphoneState !== 'idle' || !capabilities?.microphone.available) return;
  stopSpeech(); cancelMicrophone(); $('#microphone-text').value = '';
  const generation = microphoneRevision, session = authRevision;
  microphoneState = 'requesting'; text('#microphone-result', 'Esperando permiso del micrófono…'); renderMicrophone(); renderVoice();
  const capture = new PcmMicrophone({
    onLevel: ({ level, seconds }) => {
      if (generation !== microphoneRevision) return;
      scene.setInputLevel(level);
      $('#microphone-level').value = level; text('#microphone-time', `${seconds.toFixed(1)} / 15 s`);
    },
    onLimit: () => { if (generation === microphoneRevision) finishMicrophone(); },
    onEnded: () => { if (generation === microphoneRevision) { cancelMicrophone('El dispositivo se desconectó'); renderMicrophone(); renderVoice(); } },
  });
  microphone = capture;
  try {
    await capture.start($('#microphone-device').value);
    if (generation !== microphoneRevision || session !== authRevision) { capture.cancel(); return; }
    microphoneState = 'listening'; text('#microphone-result', 'Escuchando / audio local'); renderMicrophone();
    const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
    if (generation !== microphoneRevision) return;
    const previous = $('#microphone-device').value; resetMicrophoneDevices();
    for (const device of devices.filter(item => item.kind === 'audioinput')) {
      const option = document.createElement('option'); option.value = device.deviceId; option.textContent = device.label || 'Micrófono'; $('#microphone-device').append(option);
    }
    $('#microphone-device').value = previous;
  } catch (error) {
    if (generation !== microphoneRevision || session !== authRevision) return;
    cancelMicrophone(({ NotAllowedError: 'Permiso denegado. Revisa el micrófono en Windows y en esta ventana.', NotFoundError: 'No se encontró un micrófono.', NotReadableError: 'El micrófono está ocupado o no responde.', OverconstrainedError: 'Ese micrófono ya no está disponible.' })[error.name] || 'No se pudo abrir el micrófono. No se inicia escucha en segundo plano.');
    renderMicrophone(); renderVoice();
  }
}
async function finishMicrophone() {
  if (microphoneState !== 'listening' || !microphone) return;
  const generation = microphoneRevision, session = authRevision, credential = token, capture = microphone;
  microphoneState = 'processing'; text('#microphone-result', 'Micrófono cerrado / transcribiendo localmente…'); renderMicrophone(); renderVoice();
  let pcm;
  try {
    pcm = await capture.finish();
    if (generation !== microphoneRevision || session !== authRevision) return;
    microphone = null;
    const controller = new AbortController(); microphoneAbort = controller;
    const value = await transcribePcm(pcm, credential, controller.signal);
    if (generation !== microphoneRevision || session !== authRevision) return;
    cancelMicrophone(value.trim() ? 'Dictado listo' : 'No se reconoció voz');
    $('#microphone-text').value = value;
  } catch (error) {
    if (generation !== microphoneRevision || session !== authRevision) return;
    cancelMicrophone(error.message === 'microphone_too_short' ? 'Dictado demasiado corto' : error.message === 'microphone_busy' ? 'El reconocedor está ocupado. Intenta de nuevo cuando termine.' : 'No se pudo transcribir. No se guarda audio ni se reintenta automáticamente.');
  } finally { pcm?.fill(0); renderMicrophone(); renderVoice(); }
}
$('#microphone-start').addEventListener('click', startMicrophone);
$('#console-microphone').addEventListener('click', () => { selectView('assistant'); startMicrophone(); });
$('#microphone-finish').addEventListener('click', finishMicrophone);
$('#microphone-cancel').addEventListener('click', () => { cancelMicrophone('Dictado cancelado'); renderMicrophone(); renderVoice(); });
$('#microphone-text').addEventListener('input', renderMicrophone);
$('#microphone-form').addEventListener('submit', event => {
  event.preventDefault(); if (!token || !online || paused || microphoneState !== 'idle') return;
  $('#command-input').value = $('#microphone-text').value.trim(); $('#command-form').requestSubmit();
});
async function loadRules() {
  if (rulesLoaded || !token) return;
  const credential = token, generation = authRevision;
  try {
    const data = await api('alerts');
    if (generation !== authRevision) return;
    $('#alert-rules').replaceChildren(...Object.entries(data.rules).map(([resource, rule]) => {
      const row = document.createElement('div'); row.className = 'rule-row'; row.dataset.resource = resource;
      const label = document.createElement('span'); label.textContent = { cpu: 'CPU', memory: 'RAM', disk: 'Disco' }[resource]; row.append(label);
      for (const [key, name] of [['threshold_percent', 'Límite'], ['clear_percent', 'Recuperación'], ['hold_seconds', 'Espera'], ['enabled', 'Activa']]) {
        const input = document.createElement('input'); input.dataset.field = key;
        input.setAttribute('aria-label', `${label.textContent}: ${name}`);
        input.type = key === 'enabled' ? 'checkbox' : 'number';
        if (key === 'enabled') input.checked = rule[key];
        else { input.value = rule[key]; input.min = '0'; input.max = key === 'hold_seconds' ? '600' : '100'; input.step = '1'; input.required = true; }
        row.append(input);
      }
      return row;
    }));
    rulesLoaded = true; renderServices();
  } catch (error) { text('#alert-save-state', serviceError(error)); }
}
$('#alert-form').addEventListener('submit', async event => {
  event.preventDefault();
  const rules = {};
  for (const row of document.querySelectorAll('.rule-row')) {
    rules[row.dataset.resource] = Object.fromEntries([...row.querySelectorAll('input')].map(input => [input.dataset.field, input.type === 'checkbox' ? input.checked : Number(input.value)]));
  }
  const generation = authRevision; $('#save-alerts-button').disabled = true;
  try { await api('alerts/configure', { method: 'POST', body: { rules } }); if (generation === authRevision) text('#alert-save-state', 'Reglas guardadas en MySQL'); }
  catch (error) { if (generation === authRevision) text('#alert-save-state', error.status === 400 ? 'Revisa límites, recuperación y espera' : serviceError(error)); }
  finally { renderServices(); }
});
async function prepareAction(actionId) {
  if (!token) return;
  const generation = authRevision;
  try {
    const data = await api('actions/prepare', { method: 'POST', body: { action_id: actionId } });
    if (generation !== authRevision) return;
    pendingAction = { ...data, deadline: Date.now() + data.expires_in_seconds * 1000, executing: false };
    text('#action-confirm-label', data.label); text('#action-expiry', 'Confirmación válida por 60 s');
    $('#confirm-action-button').disabled = false; $('#action-dialog').showModal();
  } catch (error) { toast(serviceError(error)); }
}
$('#prepare-action-button').addEventListener('click', () => prepareAction($('#action-select').value));
$('#suggested-action-button').addEventListener('click', () => { if (proposedAction) prepareAction(proposedAction); });
$('#cancel-action-button').addEventListener('click', () => $('#action-dialog').close());
$('#action-dialog').addEventListener('close', () => { pendingAction = null; });
$('#confirm-action-button').addEventListener('click', async () => {
  if (!pendingAction || pendingAction.executing) return;
  const confirmation = pendingAction.confirmation_id, generation = authRevision;
  pendingAction.executing = true; $('#confirm-action-button').disabled = true;
  try {
    const result = await api('actions/confirm', { method: 'POST', body: { confirmation_id: confirmation } });
    if (generation === authRevision) text('#action-result', `Apertura aceptada por Windows · auditoría #${result.audit_id}`);
  } catch (error) { if (generation === authRevision) text('#action-result', serviceError(error)); }
  finally { if (generation === authRevision) $('#action-dialog').close(); }
});
async function loadHistory(append = false) {
  if (!token) { text('#history-table', 'Sesión privada desconectada'); return; }
  const generation = authRevision;
  try {
    const result = await api('history', { query: append && historyBefore ? { before: String(historyBefore) } : {} });
    if (generation !== authRevision) return;
    savedRecords = append ? [...savedRecords, ...result.records].slice(-300) : result.records;
    historyBefore = result.next_before;
    $('#history-table').replaceChildren(...savedRecords.map(item => {
      const row = document.createElement('div'); row.className = 'history-row';
      const clock = document.createElement('time'); clock.textContent = new Date(item.created_at).toLocaleString('es-CL');
      const detail = document.createElement('div'); detail.textContent = item.summary;
      const kind = document.createElement('small'); kind.textContent = `#${item.id} · ${item.kind}`; detail.append(kind);
      const state = document.createElement('span'); state.textContent = item.status;
      row.append(clock, detail, state); return row;
    }));
    if (!savedRecords.length) text('#history-table', 'Sin registros persistidos');
    $('#more-history-button').hidden = !historyBefore;
  } catch (error) { if (generation === authRevision) text('#history-table', serviceError(error)); }
}
$('#refresh-history-button').addEventListener('click', () => loadHistory());
$('#more-history-button').addEventListener('click', () => loadHistory(true));
async function loadMemory() {
  if (!token) return;
  const generation = authRevision;
  try {
    const result = await api('memory');
    if (generation !== authRevision) return;
    text('#memory-count', `${result.notes.length} / ${result.capacity}`);
    $('#memory-list').replaceChildren(...result.notes.map(note => {
      const row = document.createElement('div'); row.className = 'memory-row';
      const content = document.createElement('p'); content.textContent = note.text;
      const button = document.createElement('button'); button.className = 'icon-button';
      button.setAttribute('aria-label', 'Eliminar nota'); button.dataset.tooltip = 'Eliminar nota'; button.append(createElement(Trash2));
      button.addEventListener('click', async () => {
        if (!window.confirm('¿Eliminar esta nota de la memoria de T.H.O.R.N?')) return;
        try { await api('memory/delete', { method: 'POST', body: { id: note.id } }); if (generation === authRevision) { await loadMemory(); await loadHistory(); } }
        catch (error) { if (generation === authRevision) text('#memory-state', serviceError(error)); }
      });
      row.append(content, button); return row;
    }));
  } catch (error) { if (generation === authRevision) text('#memory-state', serviceError(error)); }
}
$('#memory-form').addEventListener('submit', async event => {
  event.preventDefault();
  const generation = authRevision, button = event.submitter; button.disabled = true;
  try {
    await api('memory/save', { method: 'POST', body: { text: $('#memory-input').value } });
    if (generation === authRevision) { $('#memory-input').value = ''; text('#memory-state', 'Nota guardada localmente'); await loadMemory(); await loadHistory(); }
  } catch (error) { if (generation === authRevision) text('#memory-state', error.status === 400 ? 'Nota inválida o memoria completa (20 notas)' : serviceError(error)); }
  finally { button.disabled = false; }
});
// La ventana propia entrega una sola sesion en memoria, nunca mediante una URL.
window.addEventListener('thorn-native-session', async event => {
  if (nativeSessionUsed || !event.detail || typeof event.detail !== 'object') return;
  nativeSessionUsed = true; const candidate = event.detail, generation = ++authRevision;
  try { sessions.validate(candidate); await api('activity', { credential: candidate.access_token }); if (generation !== authRevision) { sessions.revoke(candidate.access_token); return; } sessions.accept(candidate); token = sessions.token; conversationAutoAttempted = false; revision++; await poll(); }
  catch { toast('No se pudo iniciar la sesión de escritorio'); }
});
$('#console-toggle').addEventListener('click', () => { $('#console-output').hidden = !$('#console-output').hidden; $('#console-toggle').setAttribute('aria-expanded', String(!$('#console-output').hidden)); });
$('#close-console').addEventListener('click', () => { $('#console-output').hidden = true; $('#console-toggle').setAttribute('aria-expanded', 'false'); });
$('#command-form').addEventListener('submit', async event => {
  event.preventDefault();
  stopConversation();
  const prompt = $('#command-input').value.trim();
  const command = prompt.toLowerCase();
  $('#command-input').value = '';
  if (!command) return;
  try {
    const immediate = localControl(prompt);
    if (immediate) {
      await cancelActiveResponse();
      if (immediate === 'clear' && token) {
        await api('conversation/reset', { method: 'POST', body: {} });
        operatorTools.accept({ reply: 'Contexto de esta conversación borrado.', ui_commands: [{ type: 'clear_context' }] }, { announce: false });
      }
      consoleReply(immediate === 'clear' ? 'Contexto de esta conversación borrado.' : 'Respuesta detenida.');
      return;
    }
    if (['estado', 'status'].includes(command)) {
      const data = await api('status', { credential: '' });
      consoleReply(`Núcleo: ${data.runtime.state}\nSesión: ${duration(data.runtime.uptime_seconds)}\nMySQL: ${data.runtime.database_enabled ? 'habilitado' : 'deshabilitado'}\nEventos procesados: ${data.events.processed}\nEn cola: ${data.events.queued}\nErrores de manejadores: ${data.events.handler_errors}`);
    } else if (['sistema', 'system', 'eventos', 'events'].includes(command)) {
      if (!token) { consoleReply('La sesión privada no está conectada.'); return; }
      const data = await api(['sistema', 'system'].includes(command) ? 'system' : 'activity');
      consoleReply(JSON.stringify(data, null, 2));
    } else if (command === 'limpiar') { consoleReply('Consola vacía'); }
    else {
      if (!token) { consoleReply('La sesión privada no está conectada.'); return; }
      if (chatBusy) { toast('El modelo ya está atendiendo una consulta'); return; }
      const autoRevision = autoVoiceRevision, automatic = $('#voice-auto').checked;
      try {
        const reply = await askModel(prompt);
        if (reply && automatic && autoRevision === autoVoiceRevision) void speakText(reply, { automatic: true });
      } catch { /* askModel muestra solo errores de la sesion vigente. */ }
    }
  } catch { consoleReply('Consulta no disponible. Comprueba el enlace y la sesión.'); }
});
$('#diagnostic-button').addEventListener('click', async () => {
  if (!token) { toast('Conecta la sesión privada'); return; }
  $('#diagnostic-button').disabled = true;
  try {
    const data = await api('capabilities');
    const ms = key => Number.isFinite(timings[key]) ? `${timings[key]} ms` : 'Sin muestra';
    const advice = [data.language.state === 'engine_unavailable' && 'Comprueba Ollama y vuelve a ejecutar Diagnóstico.',
      data.language.state === 'model_missing' && 'Comprueba el perfil instalado en Ollama.',
      conversation.state === 'device_lost' && 'Selecciona un micrófono disponible y reactiva Conversación.',
      data.voice.state === 'voice_model_missing' && 'Comprueba el modelo de voz local.'].filter(Boolean);
    consoleReply(`Ollama: ${data.language.state}\nMicrófono: ${data.microphone.state} · ${conversation.state}\nVoz: ${data.voice.state}\nTranscripción: ${ms('transcription')}\nGeneración: ${ms('generation')}\nSíntesis: ${ms('synthesis')}${advice.length ? '\n\n' + advice.join('\n') : ''}`);
    await poll();
  } catch { toast('No se pudo publicar el diagnóstico'); }
  finally { $('#diagnostic-button').disabled = false; }
});
function renderDesktop() {
  if (!desktop) return;
  const state = assistantState({ online, authenticated: Boolean(token),
    playing: playback.state === 'playing', playbackState: playback.state, busy: chatBusy,
    conversation: conversation.state, microphone: microphoneState });
  desktop.update({ state, authenticated: Boolean(token), fresh: fresh(),
    cpu: telemetry?.snapshot?.cpu_percent, memory: telemetry?.snapshot?.memory_percent });
}
function tick() {
  renderDesktop();
  const now = new Date(); text('#clock-time', timeFormat.format(now)); text('#clock-date', dateFormat.format(now).toUpperCase());
  renderStatus();
  updateNetwork();
  if (pendingAction) {
    const remaining = Math.max(0, Math.ceil((pendingAction.deadline - Date.now()) / 1000));
    text('#action-expiry', remaining ? `Confirmación válida por ${remaining} s` : 'Confirmación caducada');
    $('#confirm-action-button').disabled = remaining === 0 || pendingAction.executing;
  }
  if (telemetry) { text('#sample-state', paused ? 'MUESTRA RETENIDA' : fresh() ? 'MUESTRA VIGENTE' : 'MUESTRA NO VIGENTE'); $('#sample-dot').classList.toggle('fresh', fresh()); $('.telemetry-column').classList.toggle('stale', !fresh()); }
}
window.addEventListener('resize', drawCharts);
document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (document.hidden) { cancelWeb(); stopConversation(); autoVoiceRevision++; if (speechAutomatic) stopSpeech(); cancelMicrophone(); renderMicrophone(); renderVoice(); } else if (!paused) poll(); });
window.addEventListener('pagehide', () => { sessions.close(); cancelWeb(); stopConversation(); token = ''; cancelMicrophone(); stopSpeech(); scene.dispose(); clearTimeout(timer); });
tick(); setInterval(tick, 1000); renderTelemetry(); poll();
