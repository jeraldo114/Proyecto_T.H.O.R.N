import * as THREE from 'three';

/** Anillos de presencia. El pulso viene del audio real, nunca de valores aleatorios. */
export function createCoreScene(canvas) {
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true }); }
  catch {
    document.querySelector('#webgl-fallback').hidden = false; canvas.hidden = true;
    return { setData() {}, setState() {}, setInputLevel() {}, setMotion() {}, setVoiceLevel() {}, dispose() {} };
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  camera.position.z = 8.4;
  const rig = new THREE.Group(), rings = new THREE.Group(), counter = new THREE.Group(); scene.add(rig);
  const cyan = new THREE.Color('#58caff'), muted = new THREE.Color('#3d657e');
  const live = new THREE.LineBasicMaterial({ color: cyan, transparent: true, opacity: 0.9 });
  const faint = new THREE.LineBasicMaterial({ color: '#40718c', transparent: true, opacity: 0.32 });
  const fine = new THREE.LineBasicMaterial({ color: '#a4e9ff', transparent: true, opacity: 0.65 });
  function arc(radius, start, end, material, target = rig) {
    const points = new THREE.EllipseCurve(0, 0, radius, radius, start, end).getPoints(100);
    target.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material));
  }
  arc(1.94, 0, Math.PI * 2, faint); arc(1.58, 0, Math.PI * 2, faint);
  arc(1.31, 0, Math.PI * 2, faint); arc(1.17, 0, Math.PI * 2, fine);
  for (let i = 0; i < 6; i++) {
    const start = i * Math.PI / 3;
    arc(1.79, start, start + 0.58, live, rings);
    arc(1.73, start + 0.06, start + 0.42, faint, rings);
    arc(1.48, start + 0.18, start + 0.82, fine, counter);
  }
  const ticks = [];
  for (let i = 0; i < 120; i++) {
    const theta = i / 120 * Math.PI * 2, length = i % 10 === 0 ? 0.105 : 0.035;
    ticks.push(new THREE.Vector3(Math.cos(theta) * 2.04, Math.sin(theta) * 2.04, 0),
      new THREE.Vector3(Math.cos(theta) * (2.04 + length), Math.sin(theta) * (2.04 + length), 0));
  }
  rig.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ticks), faint), rings, counter);
  const points = [];
  for (let i = 0; i < 150; i++) {
    const theta = i * 2.399963229728653, radius = 0.89 + 0.15 * Math.sin(i * 0.7);
    points.push(Math.cos(theta) * radius, Math.sin(theta) * radius, Math.sin(i * 0.8) * 0.09);
  }
  const particles = new THREE.Points(new THREE.BufferGeometry().setAttribute('position',
    new THREE.Float32BufferAttribute(points, 3)), new THREE.PointsMaterial({ color: '#67d1ff', size: 0.016, transparent: true, opacity: 0.5 }));
  rig.add(particles);
  const glowCanvas = document.createElement('canvas'); glowCanvas.width = glowCanvas.height = 128;
  const context = glowCanvas.getContext('2d'), gradient = context.createRadialGradient(64, 64, 15, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(35,142,214,0)'); gradient.addColorStop(0.52, 'rgba(35,154,237,.22)'); gradient.addColorStop(1, 'rgba(35,142,214,0)');
  context.fillStyle = gradient; context.fillRect(0, 0, 128, 128);
  const texture = new THREE.CanvasTexture(glowCanvas);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, opacity: 0.6 }));
  halo.scale.set(4.6, 4.6, 1); rig.add(halo);
  let online = false, load = 0, animation = true, reduced = false, lastFrame = 0, frame;
  let voiceLevel = 0, inputLevel = 0, envelope = 0, state = 'offline';
  function resize() {
    const { width, height } = canvas.parentElement.getBoundingClientRect(); if (!width || !height) return;
    renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.render(scene, camera);
  }
  const observer = new ResizeObserver(resize); observer.observe(canvas.parentElement);
  function render(timestamp) {
    frame = requestAnimationFrame(render);
    // NOTA DEL OPERADOR: presencia no es gastar la GPU sin control. 30 FPS y
    // pausa al ocultar; la rotacion es ornamental, el estado y el RMS son reales.
    const activeAudio = voiceLevel > 0.005 || inputLevel > 0.005 || envelope > 0.005;
    const fps = activeAudio ? 30 : !animation || reduced ? 2 : online && load > 0.7 ? 12 : 20;
    if (document.hidden || timestamp - lastFrame < 1000 / fps) return;
    const delta = Math.min((timestamp - lastFrame) / 1000, 0.1); lastFrame = timestamp;
    if (animation && !reduced) {
      rings.rotation.z += delta * (state === 'thinking' ? 0.4 : online ? 0.075 + load * 0.04 : 0.025);
      counter.rotation.z -= delta * 0.055; particles.rotation.z += delta * 0.024;
    }
    const target = state === 'hearing' ? inputLevel : voiceLevel;
    envelope += (target - envelope) * (1 - Math.exp(-delta / (target > envelope ? 0.035 : 0.12)));
    rings.scale.setScalar(1 + (animation && !reduced ? envelope * 0.16 : 0));
    fine.opacity = online ? 0.62 + envelope * 0.35 : 0.25;
    halo.material.opacity = online ? 0.6 + envelope * 0.4 : 0.18;
    live.opacity = online ? 0.88 : 0.32;
    canvas.dataset.voiceLevel = envelope.toFixed(3);
    renderer.render(scene, camera); canvas.dataset.rendered = 'true';
  }
  resize(); frame = requestAnimationFrame(render);
  return {
    setData(data) { online = data.online; load = data.fresh && Number.isFinite(data.cpu) ? data.cpu / 100 : 0; },
    setState(value) {
      state = value; live.color.copy(['blocked', 'error'].includes(state) ? new THREE.Color('#e6be7e') : online ? cyan : muted);
      canvas.dataset.assistantState = state;
    },
    setInputLevel(value) { inputLevel = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; },
    setMotion(enabled, reduce) { animation = enabled; reduced = reduce; },
    setVoiceLevel(value) { voiceLevel = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; },
    dispose() { cancelAnimationFrame(frame); observer.disconnect(); texture.dispose();
      const materials = new Set(); scene.traverse(object => { object.geometry?.dispose(); if (object.material) materials.add(object.material); });
      materials.forEach(material => material.dispose()); renderer.dispose(); },
  };
}
