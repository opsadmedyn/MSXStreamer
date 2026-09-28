// Media Syntaxis Streamer - captura de una capa HTML5 con canal alfa.
// Chromium sin pantalla -> PNG con transparencia (screencast de CDP) -> salida estándar a ritmo
// fijo (29,97 fps), para que ffmpeg lo superponga. Basado en el PoC que aguantó 19 h en el Z8.
//   node captura.mjs <url>
// Si la página no carga, se siguen enviando fotogramas transparentes y se reintenta cada 10 s:
// la composición nunca se queda sin esta entrada.
import { createRequire } from "node:module";
// playwright-core vive en el runtime (scripts/instalar-compositor.sh), no junto al código
const { chromium } = createRequire((process.env.MSXS_RUNTIME || "/home/mediasat/streamer/runtime") + "/node_modules/")("playwright-core");

const URL_CAPA = process.argv[2];
const FPS = 30000 / 1001;
const log = m => process.stderr.write(`[capa] ${new Date().toISOString()} ${m}\n`);

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const cdp = await page.context().newCDPSession(page);
await cdp.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });

let ultimo = null, recibidos = 0, enviados = 0, saltados = 0, ocupado = false;
cdp.on("Page.screencastFrame", ({ data, sessionId }) => {
  ultimo = Buffer.from(data, "base64"); recibidos++;
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});

async function cargar() {
  try {
    await page.goto(URL_CAPA, { waitUntil: "load", timeout: 15000 });
    log(`cargada ${URL_CAPA}`);
  } catch (e) {
    log(`no carga ${URL_CAPA}: ${e.message.split("\n")[0]}; reintento en 10 s`);
    setTimeout(cargar, 10000);
  }
}
await page.setContent("<html><body style='background:transparent'></body></html>");
await cdp.send("Page.startScreencast", { format: "png", maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
cargar();

// ffmpeg cerrado: no hay a quién enviar, se termina (el supervisor relanza la composición entera)
process.stdout.on("error", () => process.exit(1));
process.stdout.on("drain", () => { ocupado = false; });
const t0 = process.hrtime.bigint(); let n = 0;
function bucle() {
  if (ultimo) { if (ocupado) saltados++; else { ocupado = !process.stdout.write(ultimo); enviados++; } }
  n++;
  const siguiente = t0 + BigInt(Math.round(n * 1e9 / FPS));
  setTimeout(bucle, Math.max(0, Number(siguiente - process.hrtime.bigint()) / 1e6));
}
bucle();

let antes = { recibidos: 0, enviados: 0, saltados: 0, t: Date.now() };
setInterval(() => {
  const t = Date.now(), dt = (t - antes.t) / 1000;
  log(`chrome ${((recibidos - antes.recibidos) / dt).toFixed(1)} fps · enviados ${((enviados - antes.enviados) / dt).toFixed(1)} fps · saltados ${saltados - antes.saltados}`);
  antes = { recibidos, enviados, saltados, t };
}, 60000);
for (const s of ["SIGTERM", "SIGINT"]) process.on(s, async () => { await browser.close().catch(() => {}); process.exit(0); });
