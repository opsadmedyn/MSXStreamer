// Media Syntaxis Streamer - PNG del screencast de Chromium → fotograma yuva420p del contrato de capas.
// PNG RGBA de 8 bits (alfa premultiplicado, como lo da el screencast) → Y/U/V BT.709 en rango
// limitado + alfa recto a resolución completa. Se hace aquí y no con un ffmpeg en tubería porque
// ffmpeg retiene 2–3 imágenes antes de soltar la primera: una página quieta nunca llegaría al aire.
// Corre en un hilo aparte (Worker) por capa: el latido y la vigilancia no esperan a la conversión.
import fs from "node:fs";
import zlib from "node:zlib";
import { parentPort, isMainThread } from "node:worker_threads";

export const W = 1920, H = 1080, TAM = W * H * 5 / 2;

// Devuelve {w, h, rgba} con los píxeles premultiplicados tal cual vienen (4 bytes por píxel).
export function decodificarPNG(buf) {
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error("no es PNG");
  let pos = 8, w = 0, h = 0, tipo = 0, prof = 0, entrelazado = 0;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos), nombre = buf.toString("latin1", pos + 4, pos + 8);
    const datos = buf.subarray(pos + 8, pos + 8 + len);
    if (nombre === "IHDR") {
      w = datos.readUInt32BE(0); h = datos.readUInt32BE(4);
      prof = datos[8]; tipo = datos[9]; entrelazado = datos[12];
    } else if (nombre === "IDAT") idat.push(datos);
    else if (nombre === "IEND") break;
    pos += 12 + len;
  }
  if (prof !== 8 || (tipo !== 6 && tipo !== 2) || entrelazado) throw new Error(`PNG no admitido (tipo ${tipo}, ${prof} bits)`);
  const bpp = tipo === 6 ? 4 : 3, fila = w * bpp;
  const crudo = zlib.inflateSync(Buffer.concat(idat));
  const px = new Uint8Array(fila * h);
  for (let y = 0; y < h; y++) {
    const f = crudo[y * (fila + 1)], o = y * (fila + 1) + 1, d = y * fila, a = d - fila;
    // un bucle por tipo de filtro (el caso general dentro del bucle es el doble de lento)
    if (f === 0 || (f === 2 && !y)) {
      px.set(crudo.subarray(o, o + fila), d);
    } else if (f === 1 || (f === 4 && !y)) {                     // Paeth sin fila de arriba = Sub
      for (let x = 0; x < bpp; x++) px[d + x] = crudo[o + x];
      for (let x = bpp; x < fila; x++) px[d + x] = crudo[o + x] + px[d + x - bpp];
    } else if (f === 2) {
      for (let x = 0; x < fila; x++) px[d + x] = crudo[o + x] + px[a + x];
    } else if (f === 3) {
      for (let x = 0; x < bpp; x++) px[d + x] = crudo[o + x] + (y ? px[a + x] >> 1 : 0);
      if (y) for (let x = bpp; x < fila; x++) px[d + x] = crudo[o + x] + ((px[d + x - bpp] + px[a + x]) >> 1);
      else for (let x = bpp; x < fila; x++) px[d + x] = crudo[o + x] + (px[d + x - bpp] >> 1);
    } else if (f === 4) {
      for (let x = 0; x < bpp; x++) px[d + x] = crudo[o + x] + px[a + x];
      for (let x = bpp; x < fila; x++) {
        const izq = px[d + x - bpp], arr = px[a + x], ai = px[a + x - bpp];
        const pa = arr > ai ? arr - ai : ai - arr, pb = izq > ai ? izq - ai : ai - izq;
        const p2 = izq + arr - 2 * ai, pc = p2 < 0 ? -p2 : p2;
        px[d + x] = crudo[o + x] + (pa <= pb && pa <= pc ? izq : pb <= pc ? arr : ai);
      }
    } else throw new Error(`filtro PNG ${f}`);
  }
  if (bpp === 4) return { w, h, rgba: px };
  const rgba = new Uint8Array(w * h * 4);
  for (let i = 0, j = 0; i < w * h; i++, j += 3) {
    rgba[i * 4] = px[j]; rgba[i * 4 + 1] = px[j + 1]; rgba[i * 4 + 2] = px[j + 2]; rgba[i * 4 + 3] = 255;
  }
  return { w, h, rgba };
}

// rgba premultiplicado (W x H) → yuva420p BT.709 limitado, alfa recto. Lo que no cabe se recorta.
const INV = new Float64Array(256).map((_, a) => a ? 255 / a : 0);
export function aYUVA({ w, h, rgba }, out = Buffer.alloc(TAM)) {
  const U = W * H, V = U + W * H / 4, A = V + W * H / 4;
  out.fill(16, 0, U); out.fill(128, U, A); out.fill(0, A, TAM);
  const cw = Math.min(w, W) & ~1, ch = Math.min(h, H) & ~1;
  for (let y = 0; y < ch; y += 2) {
    for (let x = 0; x < cw; x += 2) {
      // bloque 2x2: luma del color recto de cada píxel; croma de la media ponderada por el alfa
      // (sale directa de los valores premultiplicados)
      const i0 = (y * w + x) * 4, i1 = i0 + w * 4;
      if (!(rgba[i0 + 3] | rgba[i0 + 7] | rgba[i1 + 3] | rgba[i1 + 7])) continue;   // todo transparente
      let rs = 0, gs = 0, bs = 0, as = 0;
      for (let k = 0; k < 4; k++) {
        const yy = y + (k >> 1), xx = x + (k & 1), i = (yy * w + xx) * 4, a = rgba[i + 3];
        out[A + yy * W + xx] = a;
        if (!a) continue;
        const r = rgba[i], g = rgba[i + 1], b = rgba[i + 2], inv = INV[a];
        rs += r; gs += g; bs += b; as += a;
        const l = 16.5 + (46.559 * r + 156.629 * g + 15.812 * b) * inv / 256;
        out[yy * W + xx] = l > 235 ? 235 : l;
      }
      if (!as) continue;
      const k = 255 / as / 256, c = (y >> 1) * (W >> 1) + (x >> 1);
      const u = 128.5 + (-25.664 * rs - 86.336 * gs + 112 * bs) * k;
      const v = 128.5 + (112 * rs - 101.73 * gs - 10.27 * bs) * k;
      out[U + c] = u < 16 ? 16 : u > 240 ? 240 : u;
      out[V + c] = v < 16 ? 16 : v > 240 ? 240 : v;
    }
  }
  return out;
}

export function escribir(archivo, datos) {
  const tmp = archivo + ".tmp";
  fs.writeFileSync(tmp, datos);
  fs.renameSync(tmp, archivo);
}

// Hilo de conversión: recibe {archivo, png}, escribe el fotograma y responde {ok} o {error}.
if (!isMainThread && parentPort) {
  const out = Buffer.alloc(TAM);
  parentPort.on("message", ({ archivo, png }) => {
    try {
      escribir(archivo, aYUVA(decodificarPNG(Buffer.from(png)), out));
      parentPort.postMessage({ ok: true });
    } catch (e) {
      parentPort.postMessage({ error: String(e.message) });
    }
  });
}
