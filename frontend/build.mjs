import { build } from 'esbuild';
import { copyFile, mkdir, writeFile, readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';

// El manifiesto es la lista cerrada de archivos que puede servir Django.
await mkdir('dist', { recursive: true });
const result = await build({
  entryPoints: ['src/hud.js'], bundle: true, minify: true,
  outfile: 'dist/hud.js', format: 'esm', target: ['es2022'],
  external: ['/hud/assets/conversation-vad.js', '/hud/assets/pdf-viewer.js', '/hud/assets/map-viewer.js'],
  loader: { '.woff2': 'file', '.woff': 'file', '.png': 'dataurl' },
  assetNames: '[name]-[hash]', publicPath: '/hud/assets', metafile: true,
});
await copyFile('index.html', 'dist/index.html');
const worklet = await build({ entryPoints: ['src/microphone-worklet.js'], bundle: true, minify: true,
  outfile: 'dist/microphone-worklet.js', format: 'esm', target: ['es2022'], metafile: true });
const files = Object.keys(result.metafile.outputs).map(file => basename(file));
for (const name of ['pdf-viewer', 'map-viewer']) {
  await build({ entryPoints: [`src/${name}.js`], bundle: true, minify: true, outfile: `dist/${name}.js`,
    format: 'esm', target: ['es2022'], external: ['/hud/assets/ocr.js'] });
  files.push(`${name}.js`);
}
await build({ entryPoints: ['src/ocr.js'], bundle: true, minify: true, outfile: 'dist/ocr.js',
  format: 'esm', target: ['es2022'], plugins: [{ name: 'owned-ocr-worker', setup(builder) {
    builder.onResolve({ filter: /^\.\/spawnWorker$/ }, args => {
      if (args.importer.replaceAll('\\', '/').endsWith('/tesseract.js/src/worker/browser/index.js'))
        return { path: resolve('src/ocr-worker-spawn.cjs') };
    });
  } }] });
files.push('ocr.js');
await copyFile('node_modules/tesseract.js/dist/worker.min.js', 'dist/ocr-worker.min.js'); files.push('ocr-worker.min.js');
for (const name of await readdir('node_modules/tesseract.js-core')) {
  if (!/^tesseract-core.*lstm\.wasm(\.js)?$/.test(name)) continue;
  await copyFile(`node_modules/tesseract.js-core/${name}`, `dist/${name}`); files.push(name);
}
await copyFile('node_modules/@tesseract.js-data/spa/4.0.0/spa.traineddata.gz', 'dist/spa.traineddata.gz');
files.push('spa.traineddata.gz');
for (const name of ['pdf.worker.min.mjs', 'pdf.renderer.min.mjs']) {
  await copyFile(`node_modules/pdfjs-dist/build/${name}`, `dist/${name}`); files.push(name);
}
for (const folder of ['cmaps', 'standard_fonts', 'wasm']) {
  for (const name of await readdir(`node_modules/pdfjs-dist/${folder}`)) {
    if (!/\.(bcmap|pfb|ttf|wasm|js)$/.test(name) || name.startsWith('quickjs')) continue;
    await copyFile(`node_modules/pdfjs-dist/${folder}/${name}`, `dist/${name}`); files.push(name);
  }
}
files.push(...Object.keys(worklet.metafile.outputs).map(file => basename(file)));
// El detector se carga solo en modo conversacion; ONNX ejecuta en un worker.
await build({ entryPoints: ['src/conversation-vad.js'], bundle: true, minify: true,
  outfile: 'dist/conversation-vad.js', format: 'esm', target: ['es2022'],
  external: ['/hud/assets/ort.wasm.min.mjs'],
  plugins: [{ name: 'local-ort', setup(builder) {
    builder.onResolve({ filter: /^onnxruntime-web\/wasm$/ }, () => ({ path: resolve('src/vad-ort.js') }));
  } }],
});
files.push('conversation-vad.js');
for (const [folder, names] of [
  ['@ricky0123/vad-web', ['silero_vad_v5.onnx', 'vad.worklet.bundle.min.js']],
  ['onnxruntime-web', ['ort.wasm.min.mjs', 'ort-wasm-simd-threaded.mjs', 'ort-wasm-simd-threaded.wasm']],
]) {
  for (const name of names) { await copyFile(`node_modules/${folder}/dist/${name}`, `dist/${name}`); files.push(name); }
}
const vision = await build({ entryPoints: ['src/vision-worker.js'], bundle: true, minify: true,
  outfile: 'dist/vision-worker.js', format: 'esm', target: ['es2022'], metafile: true });
files.push(...Object.keys(vision.metafile.outputs).map(file => basename(file)));
for (const name of ['vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm']) {
  await copyFile(`node_modules/@mediapipe/tasks-vision/wasm/${name}`, `dist/${name}`); files.push(name);
}
const handModel = resolve('../.models/hand_landmarker.task');
if (existsSync(handModel)) {
  const digest = createHash('sha256').update(await readFile(handModel)).digest('hex');
  if (digest !== 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1') throw new Error('vision_model_checksum_failed');
  await copyFile(handModel, 'dist/hand_landmarker.task'); files.push('hand_landmarker.task');
}
await writeFile('dist/manifest.json', JSON.stringify(['index.html', ...files]));
console.log(`HUD compilado: ${files.length + 1} archivos locales.`);
