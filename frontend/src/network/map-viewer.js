import L from 'leaflet';
import { safeSourceUrl } from './network.js';

/** Cartografia bajo demanda. No hay tiles hasta que el operador los solicita. */
export function createMapViewer(root, { places, allowed, onError }) {
  const valid = (Array.isArray(places) ? places : []).filter(place =>
    Number.isFinite(place.latitude) && Math.abs(place.latitude) <= 85 &&
    Number.isFinite(place.longitude) && Math.abs(place.longitude) <= 180).slice(0, 3);
  if (!valid.length) { root.textContent = 'La fuente no entregó coordenadas representables.'; return { dispose() {}, visible() {} }; }
  const surface = document.createElement('div'); surface.className = 'map-surface';
  surface.setAttribute('aria-label', 'Mapa interactivo del lugar solicitado');
  const controls = document.createElement('div'); controls.className = 'viewer-toolbar';
  const select = document.createElement('select'); select.setAttribute('aria-label', 'Lugar del mapa');
  for (const [index, place] of valid.entries()) select.add(new Option(place.title, String(index)));
  const load = document.createElement('button'); load.className = 'viewer-button'; load.textContent = 'Cargar mapa · OpenStreetMap';
  const message = document.createElement('p'); message.className = 'viewer-message';
  const source = document.createElement('a'); source.target = '_blank'; source.rel = 'noopener noreferrer'; source.textContent = 'Fuente de las coordenadas ↗';
  controls.append(select, load); root.append(controls, surface, message, source);
  let map, layer, marker, armed = false, disposed = false, visible = true, failures = 0, current = valid[0], center, zoom = 11;
  function describe() {
    const href = safeSourceUrl(current.source_url);
    if (href) source.href = href; else source.removeAttribute('href');
    message.textContent = `${current.title} · ${current.latitude.toFixed(5)}, ${current.longitude.toFixed(5)} · Coordenadas del artículo, no GPS.`;
    if (!map) {
      const label = document.createElement('div'); label.className = 'map-empty';
      const name = document.createElement('strong'); name.textContent = current.title;
      const coords = document.createElement('span'); coords.textContent = `${current.latitude.toFixed(5)}° / ${current.longitude.toFixed(5)}°`;
      const note = document.createElement('p'); note.textContent = 'Activa Internet y carga la cartografía para explorar el mapa. Se enviarán las teselas visibles y el origen local a OpenStreetMap.';
      label.append(name, coords, note); surface.replaceChildren(label);
    }
  }
  function pause() {
    if (map) { center = map.getCenter(); zoom = map.getZoom(); map.remove(); map = null; }
    layer = null; marker = null; describe();
  }
  function start() {
    if (disposed || !visible || !armed || !allowed()) return;
    pause(); failures = 0;
    surface.replaceChildren();
    map = L.map(surface, { zoomControl: true, attributionControl: true, maxZoom: 18, minZoom: 2,
      worldCopyJump: true }).setView(center || [current.latitude, current.longitude], zoom);
    layer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18,
      referrerPolicy: 'strict-origin-when-cross-origin', updateWhenIdle: true, keepBuffer: 1,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>' });
    layer.on('tileerror', () => {
      if (++failures >= 4) { armed = false; pause(); message.textContent = 'La cartografía no está disponible. Las coordenadas siguen siendo las recibidas de la fuente.'; }
    });
    layer.addTo(map);
    const title = document.createElement('span'); title.textContent = current.title;
    marker = L.circleMarker([current.latitude, current.longitude], { color: '#a1f6ff', fillColor: '#39bff3',
      weight: 2, fillOpacity: 0.8, radius: 7 }).addTo(map).bindTooltip(title, { permanent: true, direction: 'top' });
    map.on('moveend', () => { if (map) { center = map.getCenter(); zoom = map.getZoom(); } });
    map.invalidateSize(); load.textContent = 'Centrar ubicación'; describe();
  }
  load.onclick = () => {
    if (!allowed()) { message.textContent = 'Primero autoriza Internet en Red e Internet.'; onError?.(new Error('web_internet_consent_required')); return; }
    center = null; zoom = 11; armed = true; start();
  };
  select.onchange = () => { current = valid[Number(select.value)]; center = null; describe(); if (armed) start(); };
  const observer = new ResizeObserver(() => map?.invalidateSize()); observer.observe(surface);
  describe();
  return {
    visible(value) { visible = value; if (!value) pause(); else if (armed && allowed()) start(); },
    consent() { if (!allowed()) { armed = false; pause(); load.textContent = 'Cargar mapa · OpenStreetMap'; } },
    dispose() { disposed = true; armed = false; pause(); observer.disconnect(); root.replaceChildren(); },
  };
}
