// Media Syntaxis Streamer - capas HTML5 de una composición como "última imagen" (0.4.0).
//   node capas.mjs <carpeta de capas> <capas.json> <archivo de latido>
// Un Chromium sin pantalla por composición y una página por capa. Cada página deja su última imagen
// en <carpeta>/capaN.yuv: un fotograma crudo yuva420p 1920x1080 (BT.709, rango limitado, alfa
// recto), escrito entero en un temporal y renombrado, así el compositor nunca lee medio fotograma.
// El compositor relee ese archivo a su ritmo: el navegador nunca marca el ritmo del vídeo. Si una
// página se cuelga o este proceso cae, la capa se queda en su última imagen y el vídeo sigue.
//
// El supervisor escribe estado.json ({"capas": [{"url", "visible", "recarga"}, …]}, en el orden de
// las capas) y este proceso lo relee cada 0,5 s y aplica los cambios sin cortar nada:
//   - dirección nueva o "recarga" distinta: se abre la página nueva aparte y sustituye a la anterior
//     al llegar su primera imagen (si no carga, sigue la anterior y se reintenta de 2 a 30 s);
//   - visible=false: se escribe una imagen transparente y la página sigue viva (datos al día).
// Vigilancia: latido a cada página cada 1 s; 5 s sin respuesta o página caída → se sustituye.
// Una vez al día, o si pasa de ~400 MB de memoria JS, se renueva igual (sin corte).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { Worker } from "node:worker_threads";
import { W, H, TAM, escribir } from "./convertir.mjs";

const { chromium } = createRequire((process.env.MSXS_RUNTIME || "/home/mediasat/streamer/runtime") + "/node_modules/")("playwright-core");

const [DIR, ESTADO, LATIDO] = process.argv.slice(2);
const RENOVAR_MS = 24 * 3600 * 1000, MEMORIA_MAX = 400 * 1024 * 1024;
const log = m => process.stderr.write(`[capas] ${new Date().toISOString()} ${m}\n`);

const TRANSPARENTE = Buffer.alloc(TAM);
TRANSPARENTE.fill(16, 0, W * H);                          // Y negro (rango limitado)
TRANSPARENTE.fill(128, W * H, W * H * 3 / 2);             // U y V neutros; alfa 0

// Conversor PNG → capaN.yuv en un hilo aparte (convertir.mjs), uno por capa. Solo trabaja cuando
// la página cambia; si va atrasado, se queda solo con la imagen más reciente.
class Conversor {
  constructor(archivo) {
    this.archivo = archivo; this.libre = true; this.pendiente = null; this.hechas = 0;
    this.hilo = new Worker(new URL("./convertir.mjs", import.meta.url));
    this.hilo.on("message", m => {
      if (m.error) log(`${path.basename(this.archivo)}: imagen descartada: ${m.error}`); else this.hechas++;
      this.libre = true;
      if (this.pendiente) { const p = this.pendiente; this.pendiente = null; this.enviar(p); }
    });
    this.hilo.on("error", e => { log(`conversor: ${e.message}; termina (el supervisor lo relanza)`); process.exit(1); });
  }
  enviar(png) {
    if (!this.libre) { this.pendiente = png; return; }
    this.libre = false;
    const b = new Uint8Array(png);                          // copia propia: se transfiere al hilo
    this.hilo.postMessage({ archivo: this.archivo, png: b }, [b.buffer]);
  }
  parar() { this.hilo.terminate(); }
}

// Una página del navegador con su screencast. Llama a alImagen(png) con cada imagen nueva.
async function abrirPagina(browser, url, alImagen, alCaer) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  const p = { ctx, page, cdp, desde: Date.now(), fallos: 0 };
  try {
    await cdp.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
    cdp.on("Page.screencastFrame", ({ data, sessionId }) => {
      cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
      alImagen(Buffer.from(data, "base64"), p);
    });
    page.on("crash", () => alCaer("página caída", p));
    await page.goto(url, { waitUntil: "load", timeout: 15000 });
    await cdp.send("Page.startScreencast", { format: "png", maxWidth: W, maxHeight: H, everyNthFrame: 1 });
  } catch (e) {
    await cerrar(p);
    throw e;
  }
  return p;
}

class Capa {
  constructor(browser, n) {
    this.browser = browser; this.n = n;
    this.archivo = path.join(DIR, `capa${n}.yuv`);
    this.deseado = null; this.actual = null; this.cargando = null;
    this.visible = false; this.espera = 2000; this.reintento = 0;
    this.conversor = new Conversor(this.archivo);
  }
  clave(d) { return d ? `${d.url}\n${d.recarga || 0}` : ""; }

  // aplica el estado deseado ({url, visible, recarga} o null si la capa ya no existe)
  async aplicar(d) {
    const antes = this.deseado;
    this.deseado = d;
    const visible = !!(d && d.url && d.visible);
    if (visible !== this.visible) {
      this.visible = visible;
      if (!visible) this.transparente();
      else if (this.actual) await this.reenviar(this.actual);   // vuelve a enseñar la imagen actual
      log(`capa ${this.n}: ${visible ? "visible" : "oculta"}`);
    }
    if (this.clave(d) !== this.clave(antes)) {
      this.reintento = 0; this.espera = 2000;
      if (!d || !d.url) { await this.cerrarActual(); return; }
      this.sustituir("dirección nueva o recarga");
    }
  }

  // pide al navegador una imagen nueva (reiniciar el screencast la manda aunque nada cambie)
  async reenviar(p) {
    await p.cdp.send("Page.stopScreencast").catch(() => {});
    await p.cdp.send("Page.startScreencast", { format: "png", maxWidth: W, maxHeight: H, everyNthFrame: 1 }).catch(() => {});
  }

  // abre la página deseada aparte; sustituye a la actual al llegar su primera imagen
  async sustituir(motivo) {
    const d = this.deseado;
    if (!d || !d.url || this.cargando === this.clave(d)) return;
    const clave = this.clave(d);
    this.cargando = clave;
    log(`capa ${this.n}: abriendo ${d.url} (${motivo})`);
    let nueva = null, lista = false;
    try {
      nueva = await abrirPagina(this.browser, d.url, (png, p) => {
        nueva = p;
        if (!lista) {                         // primera imagen: pasa a ser la página de la capa
          lista = true;
          if (this.clave(this.deseado) !== clave) { cerrar(nueva); return; }
          const vieja = this.actual;
          this.actual = nueva; this.cargando = null; this.espera = 2000;
          if (vieja) cerrar(vieja);
          log(`capa ${this.n}: en el aire ${d.url}`);
        }
        if (nueva === this.actual && this.visible) this.imagen(png);
      }, (motivo, p) => { if (this.actual === p) this.sustituir(motivo); });
      // una página que no pinta nada no manda imagen: pasados 5 s, se da por buena igualmente
      setTimeout(() => {
        if (!lista && nueva && this.cargando === clave) {
          lista = true; const vieja = this.actual;
          this.actual = nueva; this.cargando = null;
          if (vieja) cerrar(vieja);
          if (this.visible) this.transparente();
          log(`capa ${this.n}: sin imagen en 5 s; se da por cargada (página vacía)`);
        }
      }, 5000);
    } catch (e) {
      if (this.cargando === clave) this.cargando = null;
      log(`capa ${this.n}: no carga ${d.url}: ${String(e.message).split("\n")[0]}; reintento en ${this.espera / 1000} s`);
      clearTimeout(this.reintento);
      this.reintento = setTimeout(() => this.sustituir("reintento"), this.espera);
      this.espera = Math.min(30000, this.espera * 2);
    }
  }

  // imagen transparente, cuando el conversor no esté a medias (si no, la pisaría con la anterior);
  // se anula si entretanto llega una imagen nueva de la página
  transparente() {
    const c = this.conversor, turno = this.turno = (this.turno || 0) + 1;
    c.pendiente = null;
    const poner = () => { if (this.turno === turno) escribir(this.archivo, TRANSPARENTE); };
    if (c.libre) return poner();
    const esperar = setInterval(() => { if (c.libre) { clearInterval(esperar); poner(); } }, 20);
  }
  imagen(png) { this.turno = (this.turno || 0) + 1; this.conversor.enviar(png); }

  async vigilar() {
    const p = this.actual;
    if (!p || this.cargando) return;
    const vivo = await Promise.race([
      p.page.evaluate("1").then(() => true, () => false),
      new Promise(r => setTimeout(() => r(false), 2000))]);
    if (p !== this.actual) return;
    p.fallos = vivo ? 0 : p.fallos + 1;
    if (p.fallos >= 5) return this.sustituir("página colgada");
    if (Date.now() - p.desde > RENOVAR_MS) return this.sustituir("renovación diaria");
    if (vivo) {
      const m = await p.cdp.send("Performance.getMetrics").catch(() => null);
      const js = m?.metrics?.find(x => x.name === "JSHeapUsedSize")?.value;
      if (js > MEMORIA_MAX) this.sustituir(`memoria JS ${Math.round(js / 1048576)} MB`);
    }
  }
  async cerrarActual() {
    const p = this.actual; this.actual = null;
    if (p) await cerrar(p);
  }
}

// cerrar una página no debe tumbar el proceso aunque ya no exista
async function cerrar(p) { await p.ctx.close().catch(() => {}); }

function leerEstado() {
  try {
    const e = JSON.parse(fs.readFileSync(ESTADO, "utf8"));
    return Array.isArray(e.capas) ? e.capas : [];
  } catch { return null; }                    // aún no escrito o a medio escribir: se deja como está
}

async function principal() {
  fs.mkdirSync(DIR, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.MSXS_CHROMIUM || undefined,
    args: ["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars", "--disable-gpu",
           "--js-flags=--max-old-space-size=512"] });
  browser.on("disconnected", () => { log("el navegador se cerró; termina (el supervisor lo relanza)"); process.exit(1); });
  const capas = [];
  let mtime = 0;
  const releer = async () => {
    let st; try { st = fs.statSync(ESTADO); } catch { return; }
    if (st.mtimeMs === mtime) return;
    const deseado = leerEstado();
    if (!deseado) return;
    mtime = st.mtimeMs;
    for (let i = 0; i < Math.max(deseado.length, capas.length); i++) {
      if (!capas[i]) capas[i] = new Capa(browser, i + 1);
      await capas[i].aplicar(deseado[i] || null);
    }
  };
  let latidos = 0;
  setInterval(() => releer().catch(e => log(`estado: ${e.message}`)), 500);
  setInterval(() => {
    for (const c of capas) c.vigilar().catch(() => {});
    // latido para el supervisor: si deja de avanzar, este proceso está colgado y lo relanza
    latidos++;
    try { fs.writeFileSync(LATIDO, `frame=${latidos}\nimagenes=${capas.map(c => c.conversor.hechas).join(",")}\n`); } catch {}
  }, 1000);
  await releer();
  for (const s of ["SIGTERM", "SIGINT"]) process.on(s, async () => {
    for (const c of capas) { c.cerrada = true; c.conversor.parar(); }
    await browser.close().catch(() => {}); process.exit(0);
  });
}

if (process.argv[1] && import.meta.url === `file://${path.resolve(process.argv[1])}`) principal();
