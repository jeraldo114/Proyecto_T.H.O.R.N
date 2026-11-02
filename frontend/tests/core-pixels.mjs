/** Verifica la imagen presentada, sin obligar a conservar el backbuffer WebGL. */
export async function visibleCorePixels(page) {
  const png = await page.locator('#core-canvas').screenshot();
  return page.evaluate(async encoded => {
    const raw = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
    const image = await createImageBitmap(new Blob([raw], { type: 'image/png' }));
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0); image.close();
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let visible = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i+3] > 20 && data[i] + data[i+1] + data[i+2] > 100) visible++;
    return visible;
  }, png.toString('base64'));
}
